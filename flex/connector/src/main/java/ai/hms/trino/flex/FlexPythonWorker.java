package ai.hms.trino.flex;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import io.trino.spi.connector.ConnectorSplit;
import io.trino.spi.connector.SchemaTableName;
import io.trino.spi.type.Type;
import org.apache.arrow.flight.Action;
import org.apache.arrow.flight.FlightClient;
import org.apache.arrow.flight.FlightRuntimeException;
import org.apache.arrow.flight.FlightStream;
import org.apache.arrow.flight.Location;
import org.apache.arrow.flight.Result;
import org.apache.arrow.flight.Ticket;
import org.apache.arrow.memory.RootAllocator;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;
import java.util.logging.Level;
import java.util.logging.Logger;
import java.util.zip.CRC32;

/**
 * Per-catalog Python subprocess + Arrow Flight client.
 *
 * <p>One instance per {@link FlexConnector}. Spawned lazily on first
 * RPC. If the subprocess dies (port handshake never arrives, RPC
 * throws), the next RPC respawns. {@link #shutdown()} is the only
 * orderly tear-down path — Trino calls it on {@code DROP CATALOG} or
 * server shutdown.
 *
 * <p>Thread-safety: subprocess lifecycle methods synchronize on the
 * instance; the Flight client itself is thread-safe per Arrow's docs,
 * so concurrent RPCs through {@code do_action} / {@code do_get} are
 * fine after the worker is up.
 */
public class FlexPythonWorker
{
    private static final Logger log = Logger.getLogger(FlexPythonWorker.class.getName());
    private static final ObjectMapper JSON = new ObjectMapper();
    private static final Duration SPAWN_TIMEOUT = Duration.ofSeconds(20);
    private static final Duration SHUTDOWN_TIMEOUT = Duration.ofSeconds(5);

    private final String catalogName;
    private final String modulePath;
    private final String pythonPath;
    // Single allocator per worker. Flight client reuses it across RPCs.
    private final RootAllocator allocator = new RootAllocator(Long.MAX_VALUE);

    // The "running connection" is the tuple (process, client). Either
    // both are present and healthy or both are null and we need to
    // respawn. AtomicReference keeps the read/write atomic without
    // dragging the whole RPC path through synchronized.
    private final AtomicReference<Live> live = new AtomicReference<>();
    // Tables cache — populated on first getTables(), invalidated on
    // respawn (Python could expose different tables after reload).
    private volatile List<TableSchema> tablesCache;
    private volatile Map<SchemaTableName, TableSchema> tableIndex;
    // Signature (size + CRC32) of the module file the worker currently has
    // loaded. Computed once per Trino query plan (cheap — the file is a few
    // KB, not per row); when it changes we send a "reload" action so the next
    // getTables/scan sees the new module. This is the entirety of the hot-swap
    // protocol: Core writes the file → Java notices → worker re-imports. No
    // DROP+CREATE on the catalog.
    //
    // We hash content rather than compare mtime: mtime has coarse, filesystem-
    // dependent granularity, so a create-then-quick-edit (or two edits in the
    // same tick) can share an mtime and be missed — and an edit that keeps the
    // file the same size (e.g. "first" → "FIRST") gives no size signal either.
    private volatile String lastModuleSignature = null;

    public FlexPythonWorker(String catalogName, String modulePath, String pythonPath)
    {
        this.catalogName = catalogName;
        this.modulePath = modulePath;
        this.pythonPath = pythonPath;
    }

    // -- Public RPC surface ----------------------------------------------

    public List<TableSchema> getTables()
    {
        checkForModuleReload();
        if (tablesCache != null) {
            return tablesCache;
        }
        synchronized (this) {
            if (tablesCache != null) {
                return tablesCache;
            }
            ensureLive();
            byte[] body = doAction("get_tables", new byte[0]);
            JsonNode arr = parseJson(body);
            List<TableSchema> out = new ArrayList<>(arr.size());
            Map<SchemaTableName, TableSchema> index = new HashMap<>();
            for (JsonNode t : arr) {
                String schema = t.path("schema").asText("default");
                String name = t.get("name").asText();
                SchemaTableName stn = new SchemaTableName(schema, name);
                ArrayNode cols = (ArrayNode) t.get("columns");
                List<ColumnSchema> columns = new ArrayList<>(cols.size());
                for (JsonNode c : cols) {
                    columns.add(new ColumnSchema(c.get("name").asText(), FlexTypes.forName(c.get("type").asText())));
                }
                TableSchema ts = new TableSchema(stn, columns);
                out.add(ts);
                index.put(stn, ts);
            }
            tablesCache = List.copyOf(out);
            tableIndex = Map.copyOf(index);
            return tablesCache;
        }
    }

    public TableSchema getTable(SchemaTableName name)
    {
        getTables();
        TableSchema ts = tableIndex.get(name);
        if (ts == null) {
            throw new IllegalArgumentException("Unknown flex table: " + name);
        }
        return ts;
    }

    public List<ConnectorSplit> getSplits(SchemaTableName table)
    {
        // Pick up module edits before scanning. The metadata path (getTables)
        // also checks, but Trino doesn't always re-list metadata when planning
        // a scan of an already-known table, so a scan could otherwise read
        // stale code. Checking here makes every scan see the current module.
        checkForModuleReload();
        // Flex has no user-facing splitting: the worker returns exactly
        // one synthetic split and never sees the query's predicate, so we
        // send only the table identity. Trino enforces WHERE post-scan.
        ensureLive();
        ObjectNode req = JSON.createObjectNode();
        ObjectNode tableNode = req.putObject("table");
        tableNode.put("schema", table.getSchemaName());
        tableNode.put("name", table.getTableName());
        byte[] body;
        try {
            body = doAction("get_splits", JSON.writeValueAsBytes(req));
        }
        catch (IOException e) {
            throw new RuntimeException("failed to serialize get_splits request", e);
        }
        JsonNode arr = parseJson(body);
        List<ConnectorSplit> splits = new ArrayList<>(arr.size());
        for (JsonNode descriptor : arr) {
            splits.add(new FlexSplit(
                    table.getSchemaName(),
                    table.getTableName(),
                    descriptor.toString()));
        }
        return splits;
    }

    /**
     * Open a Flight DoGet stream for one split. The caller owns
     * the returned {@link FlightStream} and MUST close it.
     */
    public FlightStream openStream(FlexSplit split, List<FlexColumnHandle> columns)
    {
        ensureLive();
        ObjectNode req = JSON.createObjectNode();
        ObjectNode tableNode = req.putObject("table");
        tableNode.put("schema", split.schemaName());
        tableNode.put("name", split.tableName());
        try {
            req.set("split", JSON.readTree(split.descriptorJson()));
        }
        catch (IOException e) {
            throw new RuntimeException("malformed split descriptor JSON", e);
        }
        ArrayNode cols = req.putArray("columns");
        for (FlexColumnHandle c : columns) {
            cols.add(c.name());
        }
        byte[] ticket;
        try {
            ticket = JSON.writeValueAsBytes(req);
        }
        catch (IOException e) {
            throw new RuntimeException("failed to serialize read_table ticket", e);
        }
        return live.get().client().getStream(new Ticket(ticket));
    }

    public RootAllocator allocator()
    {
        return allocator;
    }

    public synchronized void shutdown()
    {
        Live current = live.getAndSet(null);
        if (current == null) {
            return;
        }
        try {
            // Best-effort graceful shutdown action; ignore failures —
            // we kill the process either way below.
            current.client().doAction(new Action("shutdown")).forEachRemaining(r -> {});
        }
        catch (Exception ignored) {}
        try {
            current.client().close();
        }
        catch (Exception ignored) {}
        try {
            if (!current.process().waitFor(SHUTDOWN_TIMEOUT.toMillis(), java.util.concurrent.TimeUnit.MILLISECONDS)) {
                current.process().destroyForcibly();
            }
        }
        catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            current.process().destroyForcibly();
        }
        tablesCache = null;
        tableIndex = null;
        try {
            allocator.close();
        }
        catch (Exception ignored) {}
    }

    // -- Hot-swap detection ----------------------------------------------

    /**
     * Stat the module file; if its mtime has advanced since we last
     * checked, send a "reload" action to the worker and invalidate
     * our local schema cache so the next getTables() round-trip sees
     * the new tables. Cheap enough to call at the top of every
     * metadata RPC (one syscall per Trino query plan).
     *
     * <p>Race: if the file is being written exactly when we stat,
     * we may observe a partial state. The materializer uses
     * tempfile + rename so the file's content is always coherent,
     * but the mtime jump may fire one RPC early. That's fine —
     * worst case we reload twice.
     */
    private void checkForModuleReload()
    {
        if (live.get() == null) {
            return;  // first-spawn path records the signature itself
        }
        // Read failure/missing file is non-fatal; the next RPC will retry.
        // Don't throw and break an in-flight query over a transient FS hiccup.
        String sig = currentModuleSignature();
        if (sig == null) {
            return;
        }
        if (lastModuleSignature == null) {
            // First observation. Just record; nothing to reload against.
            lastModuleSignature = sig;
            return;
        }
        if (sig.equals(lastModuleSignature)) {
            return;
        }
        synchronized (this) {
            if (sig.equals(lastModuleSignature)) {
                return;  // racing thread already handled it
            }
            log.log(Level.INFO,
                    "flex worker module changed ({0} -> {1}); reloading {2}",
                    new Object[] {lastModuleSignature, sig, modulePath});
            try {
                doAction("reload", new byte[0]);
            }
            catch (Exception e) {
                // If reload itself fails, log + fall through. The next
                // RPC will retry; in the meantime the worker keeps
                // serving the previous version. Surfaces clearly in
                // Trino logs.
                log.log(Level.WARNING, "flex worker reload action failed: {0}", e.getMessage());
            }
            lastModuleSignature = sig;
            tablesCache = null;
            tableIndex = null;
        }
    }

    // -- Spawn / dispatch ------------------------------------------------

    private void ensureLive()
    {
        if (live.get() != null && live.get().process().isAlive()) {
            return;
        }
        spawn();
    }

    private synchronized void spawn()
    {
        if (live.get() != null && live.get().process().isAlive()) {
            return;
        }
        log.log(Level.INFO, "spawning flex worker for catalog {0}: python={1} module={2}",
                new Object[] {catalogName, pythonPath, modulePath});
        // Capture the module's signature BEFORE spawning — this is the version
        // the worker is about to load. Recording it now (rather than lazily in
        // checkForModuleReload) keeps lastModuleSignature in sync with the
        // LOADED module. Without it, a worker spawned out-of-band (e.g. Core
        // listing tables during reconcile) leaves the signature unrecorded, so
        // the first edit afterwards is mistaken for the "first observation" and
        // the reload is skipped — serving stale code until the next edit.
        String spawnSignature = currentModuleSignature();
        ProcessBuilder pb = new ProcessBuilder(
                pythonPath, "-m", "datapro_flex.worker",
                "--module-path", modulePath,
                "--port", "0");
        // stderr goes to Trino's stderr (where the operator can see it).
        pb.redirectError(ProcessBuilder.Redirect.INHERIT);
        Process proc;
        try {
            proc = pb.start();
        }
        catch (IOException e) {
            throw new RuntimeException("failed to spawn flex python worker (python=" + pythonPath + ")", e);
        }
        int port = readPortHandshake(proc);
        FlightClient client = FlightClient.builder(allocator, Location.forGrpcInsecure("127.0.0.1", port)).build();
        live.set(new Live(proc, client, port));
        lastModuleSignature = spawnSignature;
        // Tables cache must be re-derived against the fresh interpreter.
        tablesCache = null;
        tableIndex = null;
    }

    /**
     * Change-detection signature of the module file: its size + CRC32 of its
     * bytes, or null if missing/unreadable. Content-based (not mtime) so it's
     * immune to coarse filesystem mtime granularity and to same-size edits.
     * The file is small, so reading it once per query plan is cheap.
     */
    private String currentModuleSignature()
    {
        try {
            Path p = Paths.get(modulePath);
            if (!Files.exists(p)) {
                return null;
            }
            byte[] bytes = Files.readAllBytes(p);
            CRC32 crc = new CRC32();
            crc.update(bytes);
            return bytes.length + ":" + crc.getValue();
        }
        catch (IOException e) {
            log.log(Level.FINE, "flex worker module read failed: {0}", e.getMessage());
            return null;
        }
    }

    private int readPortHandshake(Process proc)
    {
        // Read exactly one line from stdout: {"flight_port": N}.
        // Cap how long we wait so a malformed module doesn't hang Trino.
        Instant deadline = Instant.now().plus(SPAWN_TIMEOUT);
        try (BufferedReader reader = new BufferedReader(
                new InputStreamReader(proc.getInputStream(), StandardCharsets.UTF_8))) {
            while (Instant.now().isBefore(deadline)) {
                if (!proc.isAlive() && !reader.ready()) {
                    throw new RuntimeException(
                            "flex python worker exited before handshake (exit=" + proc.exitValue() + ")");
                }
                if (reader.ready()) {
                    String line = reader.readLine();
                    if (line == null) {
                        throw new RuntimeException("flex python worker closed stdout before handshake");
                    }
                    JsonNode node = JSON.readTree(line);
                    if (node.has("flight_port")) {
                        return node.get("flight_port").asInt();
                    }
                    throw new RuntimeException("unexpected handshake line from flex worker: " + line);
                }
                Thread.sleep(50);
            }
            proc.destroyForcibly();
            throw new RuntimeException("flex python worker did not send port handshake within " + SPAWN_TIMEOUT);
        }
        catch (IOException | InterruptedException e) {
            proc.destroyForcibly();
            throw new RuntimeException("failed to read flex worker handshake", e);
        }
    }

    private byte[] doAction(String type, byte[] body)
    {
        Iterator<Result> it;
        try {
            it = live.get().client().doAction(new Action(type, body));
        }
        catch (FlightRuntimeException fre) {
            throw new RuntimeException(
                    "flex " + type + " action failed: " + flightDetail(fre), fre);
        }
        // Concatenate result chunks. Phase A actions return single-shot bodies.
        java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
        while (it.hasNext()) {
            try {
                out.write(it.next().getBody());
            }
            catch (IOException e) {
                throw new RuntimeException("failed to read flex action result", e);
            }
            catch (FlightRuntimeException fre) {
                throw new RuntimeException(
                        "flex " + type + " action failed mid-stream: " + flightDetail(fre), fre);
            }
        }
        return out.toByteArray();
    }

    /** Extracts the human-readable detail from a Flight error — typically
     * the Python traceback the worker's server-side handler caught. Falls
     * back to the bare message when the CallStatus is empty. */
    private static String flightDetail(FlightRuntimeException fre)
    {
        String description = fre.status().description();
        return (description != null && !description.isBlank()) ? description : fre.getMessage();
    }

    private static JsonNode parseJson(byte[] body)
    {
        try {
            return JSON.readTree(body);
        }
        catch (IOException e) {
            throw new RuntimeException("flex worker returned malformed JSON: " + new String(body, StandardCharsets.UTF_8), e);
        }
    }

    // -- supporting types ------------------------------------------------

    private record Live(Process process, FlightClient client, int port) {}

    public record TableSchema(SchemaTableName name, List<ColumnSchema> columns) {}

    public record ColumnSchema(String name, Type type) {}
}
