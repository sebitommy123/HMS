package ai.hms.trino.flex;

import io.trino.spi.connector.ColumnHandle;
import io.trino.spi.type.Type;

/**
 * One column from a flex table. The {@code type} is the Trino-side type
 * the Python module declared via {@code get_tables}; the {@code ordinal}
 * is the column's position in the table schema (used to look up batches
 * coming back from Arrow Flight).
 */
public record FlexColumnHandle(String name, Type type, int ordinal)
        implements ColumnHandle {}
