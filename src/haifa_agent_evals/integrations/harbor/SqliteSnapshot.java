import java.nio.file.Path;
import java.sql.DriverManager;
import java.sql.SQLException;

final class SqliteSnapshot {
    public static void main(String[] args) throws SQLException {
        String source = "jdbc:sqlite:" + Path.of(args[0]).toUri() + "?mode=ro";
        try (var connection = DriverManager.getConnection(source)) {
            try (var settings = connection.createStatement()) {
                settings.execute("PRAGMA busy_timeout = 3000");
            }
            try (var snapshot = connection.prepareStatement("VACUUM INTO ?")) {
                snapshot.setString(1, args[1]);
                snapshot.execute();
            }
        }
    }
}
