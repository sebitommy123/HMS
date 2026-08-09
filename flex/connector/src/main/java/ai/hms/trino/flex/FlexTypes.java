package ai.hms.trino.flex;

import io.trino.spi.type.BigintType;
import io.trino.spi.type.BooleanType;
import io.trino.spi.type.DateType;
import io.trino.spi.type.DoubleType;
import io.trino.spi.type.IntegerType;
import io.trino.spi.type.TimestampWithTimeZoneType;
import io.trino.spi.type.Type;
import io.trino.spi.type.VarcharType;

/**
 * Mapping between the flex contract's Trino-type strings and actual
 * Trino {@link Type} instances.
 *
 * Must stay in sync with the Python side's {@code _arrow_type_for} in
 * {@code datapro_flex.arrow_schema}. New entries here without a
 * matching Python entry (or vice versa) will fail at the worker's
 * schema validation hook, not silently mis-marshal.
 */
final class FlexTypes
{
    private FlexTypes() {}

    /** Trino's logical type used for the flex contract's JSON type-string. */
    static final Type JSON_TYPE = VarcharType.VARCHAR; // see note below

    // Trino has a real JsonType but it lives in trino-main, not trino-spi.
    // Connectors that need it usually import it from trino-plugin-toolkit.
    // For Phase A we map JSON onto VARCHAR — the user's strings round-trip
    // unchanged, downstream callers cast/parse as needed. (A future slice
    // can pull in the toolkit and switch this to JsonType.JSON.)

    static Type forName(String type)
    {
        return switch (type) {
            case "BIGINT" -> BigintType.BIGINT;
            case "INTEGER" -> IntegerType.INTEGER;
            case "DOUBLE" -> DoubleType.DOUBLE;
            case "BOOLEAN" -> BooleanType.BOOLEAN;
            case "VARCHAR" -> VarcharType.VARCHAR;
            case "DATE" -> DateType.DATE;
            case "TIMESTAMP_TZ" -> TimestampWithTimeZoneType.TIMESTAMP_TZ_MILLIS;
            case "JSON" -> JSON_TYPE;
            default -> throw new IllegalArgumentException(
                    "Unsupported flex Trino type %s. Phase A supports BIGINT, INTEGER, DOUBLE, BOOLEAN, "
                            .formatted(type) +
                            "VARCHAR, DATE, TIMESTAMP_TZ, JSON.");
        };
    }
}
