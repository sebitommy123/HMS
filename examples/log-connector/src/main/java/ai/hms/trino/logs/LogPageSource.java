package ai.hms.trino.logs;

import io.airlift.slice.Slices;
import io.trino.spi.Page;
import io.trino.spi.PageBuilder;
import io.trino.spi.block.BlockBuilder;
import io.trino.spi.connector.ColumnHandle;
import io.trino.spi.connector.ConnectorPageSource;
import io.trino.spi.connector.SourcePage;
import io.trino.spi.type.Type;
import io.trino.spi.type.VarcharType;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;
import java.util.OptionalLong;

public class LogPageSource
        implements ConnectorPageSource
{
    private static final int MAX_BATCH_ROWS = 1000;

    private final LogSplit split;
    private final List<LogColumnHandle> columns;
    private final List<Type> types;
    private final BufferedReader reader;

    private long completedBytes;
    private long completedPositions;
    private boolean finished;
    private boolean closed;

    public LogPageSource(LogSplit split, List<ColumnHandle> columns)
    {
        this.split = split;
        this.columns = new ArrayList<>(columns.size());
        this.types = new ArrayList<>(columns.size());
        for (ColumnHandle ch : columns) {
            this.columns.add((LogColumnHandle) ch);
            this.types.add(VarcharType.VARCHAR);
        }
        try {
            this.reader = Files.newBufferedReader(Paths.get(split.path()), StandardCharsets.UTF_8);
        }
        catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    @Override
    public long getCompletedBytes()
    {
        return completedBytes;
    }

    @Override
    public OptionalLong getCompletedPositions()
    {
        return OptionalLong.of(completedPositions);
    }

    @Override
    public long getReadTimeNanos()
    {
        return 0;
    }

    @Override
    public boolean isFinished()
    {
        return finished;
    }

    @Override
    public long getMemoryUsage()
    {
        return 0;
    }

    @Override
    public SourcePage getNextSourcePage()
    {
        if (finished) {
            return null;
        }

        PageBuilder builder = new PageBuilder(types);
        try {
            for (int n = 0; n < MAX_BATCH_ROWS; n++) {
                String line = reader.readLine();
                if (line == null) {
                    finished = true;
                    closeReader();
                    break;
                }
                if (line.isEmpty()) {
                    continue;
                }
                int comma = line.indexOf(',');
                String eventTime;
                String message;
                if (comma > 0) {
                    eventTime = line.substring(0, comma);
                    message = line.substring(comma + 1);
                }
                else {
                    eventTime = "";
                    message = line;
                }
                completedBytes += line.length() + 1L;

                builder.declarePosition();
                for (int i = 0; i < columns.size(); i++) {
                    BlockBuilder bb = builder.getBlockBuilder(i);
                    String value = switch (columns.get(i).name()) {
                        case "app" -> split.app();
                        case "day" -> split.day();
                        case "event_time" -> eventTime;
                        case "message" -> message;
                        default -> "";
                    };
                    VarcharType.VARCHAR.writeSlice(bb, Slices.utf8Slice(value));
                }
                completedPositions++;
            }
        }
        catch (IOException e) {
            throw new UncheckedIOException(e);
        }

        if (builder.getPositionCount() == 0) {
            return null;
        }
        Page page = builder.build();
        return SourcePage.create(page);
    }

    @Override
    public void close()
            throws IOException
    {
        closeReader();
    }

    private void closeReader()
    {
        if (closed) {
            return;
        }
        closed = true;
        try {
            reader.close();
        }
        catch (IOException ignored) {
        }
    }
}
