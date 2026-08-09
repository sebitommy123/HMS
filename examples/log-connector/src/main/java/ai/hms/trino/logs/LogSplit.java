package ai.hms.trino.logs;

import io.trino.spi.connector.ConnectorSplit;

public record LogSplit(String path, String app, String day)
        implements ConnectorSplit
{
    @Override
    public boolean isRemotelyAccessible()
    {
        // The log directory is mounted at the same path on every node, so any worker may read it.
        return true;
    }

    @Override
    public long getRetainedSizeInBytes()
    {
        return 64L + path.length() * 2L + app.length() * 2L + day.length() * 2L;
    }
}
