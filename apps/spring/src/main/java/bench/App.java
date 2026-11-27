package bench;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.UUID;
import java.util.concurrent.ThreadLocalRandom;
import java.util.regex.Pattern;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.filter.OncePerRequestFilter;
import org.springframework.web.servlet.HandlerMapping;

// TFB json + db on Spring Boot (virtual threads), production track.
// Added for parity: Actuator + Micrometer Prometheus (/metrics), Actuator liveness/readiness probes
// (/health/liveness, /health/readiness incl. db), a request-id + one-line JSON access-log filter.
@SpringBootApplication
public class App {
  public static void main(String[] args) { SpringApplication.run(App.class, args); }
}

record Message(String message) {}
record Item(String name, Integer qty) {}
record World(int id, int randomNumber) {}

@RestController
class BenchController {
  private final JdbcClient jdbc;
  BenchController(JdbcClient jdbc) { this.jdbc = jdbc; }

  @GetMapping(value = "/plaintext", produces = "text/plain")
  String plaintext() { return "Hello, World!"; }

  @GetMapping("/json")
  Message json() { return new Message("Hello, World!"); }

  @PostMapping("/echo")
  Item echo(@RequestBody Item item) { return item; }

  @GetMapping("/db")
  World db() {
    int id = ThreadLocalRandom.current().nextInt(1, 10001);
    return jdbc.sql("select id, randomnumber from world where id = ?").param(id)
        .query((rs, i) -> new World(rs.getInt(1), rs.getInt(2))).single();
  }
}

@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
class RequestLog extends OncePerRequestFilter {
  private static final Logger log = LoggerFactory.getLogger("request");
  private static final Pattern RID = Pattern.compile("^[A-Za-z0-9._-]{1,128}$");
  @Override
  protected void doFilterInternal(HttpServletRequest req, HttpServletResponse res, FilterChain chain)
      throws ServletException, IOException {
    long t0 = System.nanoTime();
    String rid = req.getHeader("x-request-id");
    if (rid == null || !RID.matcher(rid).matches()) rid = UUID.randomUUID().toString();
    res.setHeader("x-request-id", rid);
    try { chain.doFilter(req, res); } finally {
      Object route = req.getAttribute(HandlerMapping.BEST_MATCHING_PATTERN_ATTRIBUTE);
      log.atInfo().addKeyValue("method", req.getMethod()).addKeyValue("route", route == null ? "" : route)
          .addKeyValue("path", req.getRequestURI()).addKeyValue("status", res.getStatus())
          .addKeyValue("duration_ms", (System.nanoTime() - t0) / 1e6).addKeyValue("request_id", rid)
          .log("request");
    }
  }
}
