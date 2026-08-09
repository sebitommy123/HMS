package ai.hms.trino.logs;

import io.trino.spi.connector.Connector;
import io.trino.spi.connector.ConnectorContext;
import io.trino.spi.connector.ConnectorFactory;

import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.Map;

public class LogConnectorFactory
        implements ConnectorFactory
{
    @Override
    public String getName()
    {
        return "log_files";
    }

    @Override
    public Connector create(String catalogName, Map<String, String> config, ConnectorContext context)
    {
        String root = config.get("logs.root");
        if (root == null || root.isEmpty()) {
            throw new IllegalArgumentException("Required config: logs.root");
        }
        Path rootPath = Paths.get(root);
        return new LogConnector(rootPath);
    }
}
