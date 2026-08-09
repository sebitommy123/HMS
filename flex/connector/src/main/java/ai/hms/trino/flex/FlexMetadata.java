package ai.hms.trino.flex;

import io.trino.spi.connector.ColumnHandle;
import io.trino.spi.connector.ColumnMetadata;
import io.trino.spi.connector.Constraint;
import io.trino.spi.connector.ConstraintApplicationResult;
import io.trino.spi.connector.ConnectorMetadata;
import io.trino.spi.connector.ConnectorSession;
import io.trino.spi.connector.ConnectorTableHandle;
import io.trino.spi.connector.ConnectorTableMetadata;
import io.trino.spi.connector.ConnectorTableVersion;
import io.trino.spi.connector.SchemaTableName;
import io.trino.spi.predicate.TupleDomain;

import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * Metadata side of the connector. Walks Python's {@code get_tables}
 * exactly once per query (via the worker's caching layer) and exposes
 * the result as Trino schemas / tables / columns.
 *
 * Predicate pushdown lives here too — {@link #applyFilter} is the hook
 * where Trino offers us its TupleDomain and we decide what to keep on
 * the Python side vs. let Trino post-filter. (A5 adds the real
 * implementation; A1 ships the structural plumbing.)
 */
public class FlexMetadata
        implements ConnectorMetadata
{
    private final FlexPythonWorker worker;

    public FlexMetadata(FlexPythonWorker worker)
    {
        this.worker = worker;
    }

    @Override
    public List<String> listSchemaNames(ConnectorSession session)
    {
        return worker.getTables().stream()
                .map(t -> t.name().getSchemaName())
                .distinct()
                .toList();
    }

    @Override
    public List<SchemaTableName> listTables(ConnectorSession session, Optional<String> schema)
    {
        return worker.getTables().stream()
                .map(FlexPythonWorker.TableSchema::name)
                .filter(n -> schema.isEmpty() || n.getSchemaName().equals(schema.get()))
                .toList();
    }

    @Override
    public ConnectorTableHandle getTableHandle(
            ConnectorSession session,
            SchemaTableName tableName,
            Optional<ConnectorTableVersion> startVersion,
            Optional<ConnectorTableVersion> endVersion)
    {
        boolean exists = worker.getTables().stream()
                .anyMatch(t -> t.name().equals(tableName));
        return exists ? FlexTableHandle.of(tableName) : null;
    }

    @Override
    public ConnectorTableMetadata getTableMetadata(ConnectorSession session, ConnectorTableHandle table)
    {
        FlexTableHandle handle = (FlexTableHandle) table;
        FlexPythonWorker.TableSchema ts = worker.getTable(handle.name());
        List<ColumnMetadata> cols = ts.columns().stream()
                .map(c -> new ColumnMetadata(c.name(), c.type()))
                .toList();
        return new ConnectorTableMetadata(ts.name(), cols);
    }

    @Override
    public Map<String, ColumnHandle> getColumnHandles(ConnectorSession session, ConnectorTableHandle table)
    {
        FlexTableHandle handle = (FlexTableHandle) table;
        FlexPythonWorker.TableSchema ts = worker.getTable(handle.name());
        Map<String, ColumnHandle> out = new LinkedHashMap<>();
        List<FlexPythonWorker.ColumnSchema> cols = ts.columns();
        for (int i = 0; i < cols.size(); i++) {
            FlexPythonWorker.ColumnSchema c = cols.get(i);
            out.put(c.name(), new FlexColumnHandle(c.name(), c.type(), i));
        }
        return out;
    }

    @Override
    public ColumnMetadata getColumnMetadata(ConnectorSession session, ConnectorTableHandle table, ColumnHandle columnHandle)
    {
        FlexColumnHandle handle = (FlexColumnHandle) columnHandle;
        return new ColumnMetadata(handle.name(), handle.type());
    }

    @Override
    public Optional<ConstraintApplicationResult<ConnectorTableHandle>> applyFilter(
            ConnectorSession session,
            ConnectorTableHandle table,
            Constraint constraint)
    {
        FlexTableHandle handle = (FlexTableHandle) table;

        // Project the engine's summary onto our column handles. Anything
        // expressed on a handle we don't recognize is ignored (it stays
        // in `remaining` for Trino to enforce).
        TupleDomain<FlexColumnHandle> incoming = constraint.getSummary()
                .transformKeys(FlexColumnHandle.class::cast);

        TupleDomain<FlexColumnHandle> merged = handle.constraint().intersect(incoming);
        if (merged.equals(handle.constraint())) {
            // Nothing new — without returning empty here Trino's planner
            // would re-call applyFilter forever.
            return Optional.empty();
        }

        FlexTableHandle newHandle = new FlexTableHandle(handle.name(), merged);

        // Crucially, we leave the *entire* summary as remaining. Flex
        // modules never see the predicate (the contract has no push-down
        // hook), so Trino always enforces it post-scan for correctness.
        // We still record it on the handle for potential future use, but
        // it is never ferried to Python.
        return Optional.of(new ConstraintApplicationResult<>(
                newHandle,
                constraint.getSummary(),
                constraint.getExpression(),
                false));  // precalculateStatistics: nothing to precompute here.
    }

    /**
     * Quick lookup used by the split manager + page source to translate
     * column names back into the table's declared types and ordinals,
     * without re-walking the metadata.
     */
    Map<String, FlexColumnHandle> columnHandlesByName(SchemaTableName tableName)
    {
        FlexPythonWorker.TableSchema ts = worker.getTable(tableName);
        Map<String, FlexColumnHandle> out = new HashMap<>();
        List<FlexPythonWorker.ColumnSchema> cols = ts.columns();
        for (int i = 0; i < cols.size(); i++) {
            FlexPythonWorker.ColumnSchema c = cols.get(i);
            out.put(c.name(), new FlexColumnHandle(c.name(), c.type(), i));
        }
        return out;
    }

}
