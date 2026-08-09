package ai.hms.trino.logs;

import io.trino.spi.connector.ConnectorSession;
import io.trino.spi.connector.ConnectorSplit;
import io.trino.spi.connector.ConnectorSplitManager;
import io.trino.spi.connector.ConnectorSplitSource;
import io.trino.spi.connector.ConnectorTableHandle;
import io.trino.spi.connector.ConnectorTransactionHandle;
import io.trino.spi.connector.Constraint;
import io.trino.spi.connector.DynamicFilter;
import io.trino.spi.connector.FixedSplitSource;

import java.io.IOException;
import java.nio.file.FileVisitResult;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.SimpleFileVisitor;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.ArrayList;
import java.util.List;

public class LogSplitManager
        implements ConnectorSplitManager
{
    private final Path root;

    public LogSplitManager(Path root)
    {
        this.root = root;
    }

    @Override
    public ConnectorSplitSource getSplits(
            ConnectorTransactionHandle transaction,
            ConnectorSession session,
            ConnectorTableHandle table,
            DynamicFilter dynamicFilter,
            Constraint constraint)
    {
        List<ConnectorSplit> splits = new ArrayList<>();
        try {
            Files.walkFileTree(root, new SimpleFileVisitor<Path>()
            {
                @Override
                public FileVisitResult visitFile(Path file, BasicFileAttributes attrs)
                {
                    String name = file.getFileName().toString();
                    if (!name.endsWith(".log")) {
                        return FileVisitResult.CONTINUE;
                    }
                    Path rel = root.relativize(file);
                    if (rel.getNameCount() < 2) {
                        // We expect <app>/<day>.log; skip anything else.
                        return FileVisitResult.CONTINUE;
                    }
                    String app = rel.getName(0).toString();
                    String day = name.substring(0, name.length() - ".log".length());
                    splits.add(new LogSplit(file.toAbsolutePath().toString(), app, day));
                    return FileVisitResult.CONTINUE;
                }
            });
        }
        catch (IOException e) {
            throw new RuntimeException("Error walking log root " + root, e);
        }
        return new FixedSplitSource(splits);
    }
}
