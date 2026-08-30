package ai.hms.trino.flex;

import io.trino.spi.connector.ColumnHandle;
import io.trino.spi.connector.ConnectorSession;
import io.trino.spi.connector.ConnectorSplit;
import io.trino.spi.connector.ConnectorSplitManager;
import io.trino.spi.connector.ConnectorSplitSource;
import io.trino.spi.connector.ConnectorTableHandle;
import io.trino.spi.connector.ConnectorTransactionHandle;
import io.trino.spi.connector.Constraint;
import io.trino.spi.connector.FixedSplitSource;

import java.util.List;
import java.util.Set;

/**
 * Delegates to {@link FlexPythonWorker#getSplits} and wraps the result
 * as a {@link FixedSplitSource}. Per Trino's contract this is called
 * once per table scan in a query.
 *
 * Flex always produces exactly one split, and the Python module never
 * sees the query predicate — {@link FlexMetadata#applyFilter} records it
 * on the handle for the planner, but Trino enforces it post-scan, not the
 * module. So this manager passes only the table identity to the worker.
 */
public class FlexSplitManager
        implements ConnectorSplitManager
{
    private final FlexPythonWorker worker;

    public FlexSplitManager(FlexPythonWorker worker)
    {
        this.worker = worker;
    }

    @Override
    public ConnectorSplitSource getSplits(
            ConnectorTransactionHandle transaction,
            ConnectorSession session,
            ConnectorTableHandle table,
            Set<ColumnHandle> columns,
            Constraint constraint)
    {
        FlexTableHandle handle = (FlexTableHandle) table;
        List<ConnectorSplit> splits = worker.getSplits(handle.name());
        return new FixedSplitSource(splits);
    }
}
