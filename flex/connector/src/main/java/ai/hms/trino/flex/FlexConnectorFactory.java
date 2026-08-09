package ai.hms.trino.flex;

import io.trino.spi.connector.Connector;
import io.trino.spi.connector.ConnectorContext;
import io.trino.spi.connector.ConnectorFactory;

import java.util.Map;

/**
 * Validates catalog properties and constructs a {@link FlexConnector}.
 *
 * Catalog DDL example:
 * <pre>
 *   CREATE CATALOG users USING flex WITH (
 *     "flex.module_path" = "/var/datapro-flex/users/abc123.py"
 *   );
 * </pre>
 *
 * Properties:
 *   flex.module_path   (required) absolute path to the user's Python module on the Trino container
 *   flex.python        (optional, default `/opt/flex-venv/bin/python3`) interpreter to invoke
 *
 * The worker is spawned lazily on first table access — failed module load
 * surfaces as an exception there, not at CREATE CATALOG time (we want
 * Core to be able to register the catalog row even if the module has a
 * temporary issue; queries will fail loudly until it's fixed).
 */
public class FlexConnectorFactory
        implements ConnectorFactory
{
    private static final String DEFAULT_PYTHON = "/opt/flex-venv/bin/python3";

    @Override
    public String getName()
    {
        return "flex";
    }

    @Override
    public Connector create(String catalogName, Map<String, String> config, ConnectorContext context)
    {
        String modulePath = config.get("flex.module_path");
        if (modulePath == null || modulePath.isBlank()) {
            throw new IllegalArgumentException(
                    "Required catalog property: flex.module_path (absolute path to the Python module)");
        }
        String python = config.getOrDefault("flex.python", DEFAULT_PYTHON);
        return new FlexConnector(catalogName, modulePath, python);
    }
}
