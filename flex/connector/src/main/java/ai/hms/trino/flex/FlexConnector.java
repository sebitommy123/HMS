package ai.hms.trino.flex;

import io.trino.spi.connector.Connector;
import io.trino.spi.connector.ConnectorMetadata;
import io.trino.spi.connector.ConnectorPageSourceProvider;
import io.trino.spi.connector.ConnectorSession;
import io.trino.spi.connector.ConnectorSplitManager;
import io.trino.spi.connector.ConnectorTransactionHandle;
import io.trino.spi.transaction.IsolationLevel;

/**
 * Per-catalog connector. Owns the long-lived {@link FlexPythonWorker} (one
 * Python subprocess + Flight client per catalog) and hands out the metadata
 * / split / page-source providers that delegate into it.
 */
public class FlexConnector
        implements Connector
{
    private final FlexPythonWorker worker;
    private final FlexMetadata metadata;
    private final FlexSplitManager splitManager;
    private final FlexPageSourceProvider pageSourceProvider;

    public FlexConnector(String catalogName, String modulePath, String pythonPath)
    {
        this.worker = new FlexPythonWorker(catalogName, modulePath, pythonPath);
        this.metadata = new FlexMetadata(worker);
        this.splitManager = new FlexSplitManager(worker);
        this.pageSourceProvider = new FlexPageSourceProvider(worker);
    }

    @Override
    public ConnectorTransactionHandle beginTransaction(IsolationLevel level, boolean readOnly, boolean autoCommit)
    {
        return FlexTransactionHandle.INSTANCE;
    }

    @Override
    public ConnectorMetadata getMetadata(ConnectorSession session, ConnectorTransactionHandle transaction)
    {
        return metadata;
    }

    @Override
    public ConnectorSplitManager getSplitManager()
    {
        return splitManager;
    }

    @Override
    public ConnectorPageSourceProvider getPageSourceProvider()
    {
        return pageSourceProvider;
    }

    @Override
    public void shutdown()
    {
        worker.shutdown();
    }
}
