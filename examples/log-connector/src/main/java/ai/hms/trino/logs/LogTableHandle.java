package ai.hms.trino.logs;

import io.trino.spi.connector.ConnectorTableHandle;
import io.trino.spi.connector.SchemaTableName;

public record LogTableHandle(SchemaTableName name)
        implements ConnectorTableHandle {}
