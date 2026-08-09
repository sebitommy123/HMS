package ai.hms.trino.flex;

import io.trino.spi.connector.ConnectorTransactionHandle;

/**
 * Singleton — flex catalogs are read-only and stateless, so transactions
 * are a no-op. Same pattern as the log-connector example.
 */
public enum FlexTransactionHandle
        implements ConnectorTransactionHandle
{
    INSTANCE
}
