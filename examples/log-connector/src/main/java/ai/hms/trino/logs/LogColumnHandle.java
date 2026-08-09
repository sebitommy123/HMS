package ai.hms.trino.logs;

import io.trino.spi.connector.ColumnHandle;

public record LogColumnHandle(String name, int ordinal)
        implements ColumnHandle {}
