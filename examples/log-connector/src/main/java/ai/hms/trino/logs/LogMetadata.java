package ai.hms.trino.logs;

import io.trino.spi.connector.ColumnHandle;
import io.trino.spi.connector.ColumnMetadata;
import io.trino.spi.connector.ConnectorMetadata;
import io.trino.spi.connector.ConnectorSession;
import io.trino.spi.connector.ConnectorTableHandle;
import io.trino.spi.connector.ConnectorTableMetadata;
import io.trino.spi.connector.ConnectorTableVersion;
import io.trino.spi.connector.SchemaTableName;
import io.trino.spi.type.VarcharType;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

public class LogMetadata
        implements ConnectorMetadata
{
    private static final String SCHEMA = "default";
    private static final String TABLE = "entries";
    private static final SchemaTableName TABLE_NAME = new SchemaTableName(SCHEMA, TABLE);

    private static final List<ColumnMetadata> COLUMNS = List.of(
            new ColumnMetadata("app", VarcharType.VARCHAR),
            new ColumnMetadata("day", VarcharType.VARCHAR),
            new ColumnMetadata("event_time", VarcharType.VARCHAR),
            new ColumnMetadata("message", VarcharType.VARCHAR));

    @Override
    public List<String> listSchemaNames(ConnectorSession session)
    {
        return List.of(SCHEMA);
    }

    @Override
    public ConnectorTableHandle getTableHandle(
            ConnectorSession session,
            SchemaTableName tableName,
            Optional<ConnectorTableVersion> startVersion,
            Optional<ConnectorTableVersion> endVersion)
    {
        if (TABLE_NAME.equals(tableName)) {
            return new LogTableHandle(TABLE_NAME);
        }
        return null;
    }

    @Override
    public ConnectorTableMetadata getTableMetadata(ConnectorSession session, ConnectorTableHandle table)
    {
        return new ConnectorTableMetadata(TABLE_NAME, COLUMNS);
    }

    @Override
    public List<SchemaTableName> listTables(ConnectorSession session, Optional<String> schema)
    {
        if (schema.isEmpty() || schema.get().equals(SCHEMA)) {
            return List.of(TABLE_NAME);
        }
        return List.of();
    }

    @Override
    public Map<String, ColumnHandle> getColumnHandles(ConnectorSession session, ConnectorTableHandle table)
    {
        Map<String, ColumnHandle> handles = new LinkedHashMap<>();
        for (int i = 0; i < COLUMNS.size(); i++) {
            ColumnMetadata col = COLUMNS.get(i);
            handles.put(col.getName(), new LogColumnHandle(col.getName(), i));
        }
        return handles;
    }

    @Override
    public ColumnMetadata getColumnMetadata(ConnectorSession session, ConnectorTableHandle table, ColumnHandle columnHandle)
    {
        LogColumnHandle handle = (LogColumnHandle) columnHandle;
        return COLUMNS.get(handle.ordinal());
    }
}
