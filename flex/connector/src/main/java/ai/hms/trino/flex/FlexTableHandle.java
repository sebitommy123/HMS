package ai.hms.trino.flex;

import io.trino.spi.connector.ConnectorTableHandle;
import io.trino.spi.connector.SchemaTableName;
import io.trino.spi.predicate.TupleDomain;

/**
 * Opaque handle carried through the planner. Holds the schema-qualified
 * name plus the *effective* predicate domain after pushdown — when
 * {@link FlexMetadata#applyFilter} compresses Trino's TupleDomain into
 * something the Python module can prune on, the residual goes back to
 * Trino and the kept-in-handle part travels to the split manager.
 */
public record FlexTableHandle(SchemaTableName name, TupleDomain<FlexColumnHandle> constraint)
        implements ConnectorTableHandle
{
    public static FlexTableHandle of(SchemaTableName name)
    {
        return new FlexTableHandle(name, TupleDomain.all());
    }
}
