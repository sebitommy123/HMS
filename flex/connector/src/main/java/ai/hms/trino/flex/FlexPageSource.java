package ai.hms.trino.flex;

import io.airlift.slice.Slices;
import io.trino.spi.Page;
import io.trino.spi.PageBuilder;
import io.trino.spi.block.BlockBuilder;
import io.trino.spi.connector.ColumnHandle;
import io.trino.spi.connector.ConnectorPageSource;
import io.trino.spi.connector.SourcePage;
import io.trino.spi.type.BigintType;
import io.trino.spi.type.BooleanType;
import io.trino.spi.type.DateTimeEncoding;
import io.trino.spi.type.DateType;
import io.trino.spi.type.DoubleType;
import io.trino.spi.type.IntegerType;
import io.trino.spi.type.TimeZoneKey;
import io.trino.spi.type.TimestampWithTimeZoneType;
import io.trino.spi.type.Type;
import io.trino.spi.type.VarcharType;
import org.apache.arrow.flight.FlightRuntimeException;
import org.apache.arrow.flight.FlightStream;
import org.apache.arrow.vector.BigIntVector;
import org.apache.arrow.vector.BitVector;
import org.apache.arrow.vector.DateDayVector;
import org.apache.arrow.vector.FieldVector;
import org.apache.arrow.vector.Float8Vector;
import org.apache.arrow.vector.IntVector;
import org.apache.arrow.vector.TimeStampMilliTZVector;
import org.apache.arrow.vector.VarCharVector;
import org.apache.arrow.vector.VectorSchemaRoot;

import java.util.ArrayList;
import java.util.List;
import java.util.OptionalLong;

/**
 * Pulls Arrow record batches from the Python worker (via Flight DoGet)
 * and adapts them to Trino {@link Page}s — one Trino page per Arrow
 * batch.
 *
 * The Arrow→Trino value copy is per-type and stays in lockstep with
 * the Python-side type table in {@code datapro_flex.arrow_schema}.
 */
public class FlexPageSource
        implements ConnectorPageSource
{
    private final FlexPythonWorker worker;
    private final FlexSplit split;
    private final List<FlexColumnHandle> columns;
    private final List<Type> types;

    private FlightStream stream;
    private long completedPositions;
    private boolean finished;
    private boolean closed;

    public FlexPageSource(FlexPythonWorker worker, FlexSplit split, List<ColumnHandle> columnHandles)
    {
        this.worker = worker;
        this.split = split;
        this.columns = new ArrayList<>(columnHandles.size());
        this.types = new ArrayList<>(columnHandles.size());
        for (ColumnHandle ch : columnHandles) {
            FlexColumnHandle h = (FlexColumnHandle) ch;
            this.columns.add(h);
            this.types.add(h.type());
        }
    }

    @Override
    public long getCompletedBytes()
    {
        // Arrow Flight doesn't easily expose wire bytes per batch; report
        // 0 rather than fake a number. Trino uses this for progress only.
        return 0;
    }

    @Override
    public OptionalLong getCompletedPositions()
    {
        return OptionalLong.of(completedPositions);
    }

    @Override
    public long getReadTimeNanos()
    {
        return 0;
    }

    @Override
    public boolean isFinished()
    {
        return finished;
    }

    @Override
    public long getMemoryUsage()
    {
        return 0;
    }

    @Override
    public SourcePage getNextSourcePage()
    {
        if (finished) {
            return null;
        }
        if (stream == null) {
            stream = worker.openStream(split, columns);
        }
        boolean hasMore;
        try {
            hasMore = stream.next();
        }
        catch (Exception e) {
            close();
            // Arrow Flight propagates server-side Python exceptions as
            // FlightRuntimeException with the Python traceback inside
            // CallStatus.description(). Pull that out so the operator
            // sees `read_table() takes 1 positional argument but 2
            // were given\n  File "...", line N, in ...` instead of a
            // generic "stream failed" message.
            String detail = e.getMessage();
            if (e instanceof FlightRuntimeException fre) {
                String description = fre.status().description();
                if (description != null && !description.isBlank()) {
                    detail = description;
                }
            }
            throw new RuntimeException(
                    "flex read_table failed for " + split + ": " + detail, e);
        }
        if (!hasMore) {
            finished = true;
            close();
            return null;
        }
        VectorSchemaRoot root = stream.getRoot();
        Page page = buildPage(root);
        completedPositions += root.getRowCount();
        return SourcePage.create(page);
    }

    @Override
    public void close()
    {
        if (closed) {
            return;
        }
        closed = true;
        if (stream != null) {
            try {
                stream.close();
            }
            catch (Exception ignored) {}
            stream = null;
        }
    }

    private Page buildPage(VectorSchemaRoot root)
    {
        int rows = root.getRowCount();
        PageBuilder builder = new PageBuilder(rows, types);
        for (int i = 0; i < columns.size(); i++) {
            FlexColumnHandle col = columns.get(i);
            FieldVector vec = root.getVector(col.name());
            if (vec == null) {
                throw new IllegalStateException(
                        "flex batch is missing requested column: " + col.name());
            }
            copyColumn(builder.getBlockBuilder(i), col.type(), vec, rows);
        }
        builder.declarePositions(rows);
        return builder.build();
    }

    private static void copyColumn(BlockBuilder out, Type type, FieldVector vec, int rows)
    {
        if (type == BigintType.BIGINT) {
            BigIntVector v = (BigIntVector) vec;
            for (int r = 0; r < rows; r++) {
                if (v.isNull(r)) { out.appendNull(); }
                else { BigintType.BIGINT.writeLong(out, v.get(r)); }
            }
            return;
        }
        if (type == IntegerType.INTEGER) {
            IntVector v = (IntVector) vec;
            for (int r = 0; r < rows; r++) {
                if (v.isNull(r)) { out.appendNull(); }
                else { IntegerType.INTEGER.writeLong(out, v.get(r)); }
            }
            return;
        }
        if (type == DoubleType.DOUBLE) {
            Float8Vector v = (Float8Vector) vec;
            for (int r = 0; r < rows; r++) {
                if (v.isNull(r)) { out.appendNull(); }
                else { DoubleType.DOUBLE.writeDouble(out, v.get(r)); }
            }
            return;
        }
        if (type == BooleanType.BOOLEAN) {
            BitVector v = (BitVector) vec;
            for (int r = 0; r < rows; r++) {
                if (v.isNull(r)) { out.appendNull(); }
                else { BooleanType.BOOLEAN.writeBoolean(out, v.get(r) != 0); }
            }
            return;
        }
        if (type instanceof VarcharType varchar) {
            VarCharVector v = (VarCharVector) vec;
            for (int r = 0; r < rows; r++) {
                if (v.isNull(r)) { out.appendNull(); }
                else { varchar.writeSlice(out, Slices.wrappedBuffer(v.get(r))); }
            }
            return;
        }
        if (type == DateType.DATE) {
            DateDayVector v = (DateDayVector) vec;
            for (int r = 0; r < rows; r++) {
                if (v.isNull(r)) { out.appendNull(); }
                else { DateType.DATE.writeLong(out, v.get(r)); }
            }
            return;
        }
        if (type == TimestampWithTimeZoneType.TIMESTAMP_TZ_MILLIS) {
            TimeStampMilliTZVector v = (TimeStampMilliTZVector) vec;
            for (int r = 0; r < rows; r++) {
                if (v.isNull(r)) {
                    out.appendNull();
                }
                else {
                    // Arrow stores epoch ms; the contract pins tz=UTC so
                    // the offset is always 0. Trino's wire format packs
                    // (millis, tz key) into a single long.
                    long packed = DateTimeEncoding.packDateTimeWithZone(v.get(r), TimeZoneKey.UTC_KEY);
                    TimestampWithTimeZoneType.TIMESTAMP_TZ_MILLIS.writeLong(out, packed);
                }
            }
            return;
        }
        throw new IllegalStateException("flex page source: no copy strategy for type " + type);
    }
}
