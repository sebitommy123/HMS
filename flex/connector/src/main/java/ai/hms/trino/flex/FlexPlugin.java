package ai.hms.trino.flex;

import io.trino.spi.Plugin;
import io.trino.spi.connector.ConnectorFactory;

import java.util.List;

/**
 * SPI entry point. Registered via
 * `META-INF/services/io.trino.spi.Plugin`, loaded by Trino on startup
 * for every JAR in `/usr/lib/trino/plugin/flex/`.
 */
public class FlexPlugin
        implements Plugin
{
    @Override
    public Iterable<ConnectorFactory> getConnectorFactories()
    {
        return List.of(new FlexConnectorFactory());
    }
}
