package ai.hms.trino.flex;

import io.trino.spi.connector.ConnectorSplit;

/**
 * The single unit of work flex schedules per table scan. Flex has no
 * user-facing splitting — the worker synthesizes exactly one split
 * (an empty {@code {}} descriptor) via the {@code get_splits} action.
 * The descriptor is opaque to Java and ferried back to Python verbatim
 * when {@code read_table} runs (which ignores it).
 *
 * @param schemaName     the catalog-relative schema (Python's table.schema, or "default")
 * @param tableName      the logical table name
 * @param descriptorJson JSON-encoded dict from the get_splits action, sent back as-is
 */
public record FlexSplit(String schemaName, String tableName, String descriptorJson)
        implements ConnectorSplit
{
    @Override
    public boolean isRemotelyAccessible()
    {
        // All Trino workers reach the same Python worker subprocess on the
        // same node where the coordinator schedules them. For single-node
        // dev that's trivially true; for multi-node clusters the flex
        // worker model needs revisiting (currently assumes one Python
        // per catalog, coordinator-local).
        return true;
    }

    @Override
    public long getRetainedSizeInBytes()
    {
        return 96L + (long) descriptorJson.length() * 2L
                + (long) schemaName.length() * 2L
                + (long) tableName.length() * 2L;
    }
}
