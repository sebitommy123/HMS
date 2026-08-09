package ai.hms.trino.logs;

import io.trino.spi.connector.ConnectorTransactionHandle;

public enum LogTransactionHandle
        implements ConnectorTransactionHandle
{
    INSTANCE
}
