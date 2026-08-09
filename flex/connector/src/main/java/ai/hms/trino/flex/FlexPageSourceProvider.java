package ai.hms.trino.flex;

import io.trino.spi.connector.ColumnHandle;
import io.trino.spi.connector.ConnectorPageSource;
import io.trino.spi.connector.ConnectorPageSourceProvider;
import io.trino.spi.connector.ConnectorSession;
import io.trino.spi.connector.ConnectorSplit;
import io.trino.spi.connector.ConnectorTableHandle;
import io.trino.spi.connector.ConnectorTransactionHandle;
import io.trino.spi.connector.DynamicFilter;

import java.util.List;

/**
 * Hands out one {@link FlexPageSource} per (split × set of requested
 * columns). The page source is the thing that actually pulls Arrow
 * record batches from the Python worker and turns them into Trino
 * {@code Page}s.
 */
public class FlexPageSourceProvider
        implements ConnectorPageSourceProvider
{
    private final FlexPythonWorker worker;

    public FlexPageSourceProvider(FlexPythonWorker worker)
    {
        this.worker = worker;
    }

    @Override
    public ConnectorPageSource createPageSource(
            ConnectorTransactionHandle transaction,
            ConnectorSession session,
            ConnectorSplit split,
            ConnectorTableHandle table,
            List<ColumnHandle> columns,
            DynamicFilter dynamicFilter)
    {
        return new FlexPageSource(worker, (FlexSplit) split, columns);
    }
}
