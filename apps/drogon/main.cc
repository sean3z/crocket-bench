// TFB plaintext/json/db on Drogon 1.9, production track. Fast DbClient (async, per-loop connections).
// Added for parity: PromExporter plugin (/metrics) with a drogon::monitoring::Histogram per route, a
// pre-routing/post-handling advice for x-request-id and one JSON access-log line per request (async
// AsyncFileLogger-free: buffered stdout write), /healthz + /readyz handlers, DbClient 10 s timeout,
// idle-connection and body-size limits.
#include <drogon/drogon.h>
#include <drogon/plugins/PromExporter.h>
#include <drogon/utils/monitoring/Metric.h>
#include <atomic>
#include <format>
#include <drogon/utils/monitoring/Collector.h>
#include <chrono>
#include <cstdio>
#include <random>

using namespace drogon;
using Clock = std::chrono::steady_clock;

static int random_id() {
  thread_local std::mt19937 rng{std::random_device{}()};
  thread_local std::uniform_int_distribution<int> d{1, 10000};
  return d(rng);
}

// drogon::monitoring::Histogram crashes on its first observe() when maxAge is 0 (its first time bucket
// has no bucket slots; same code in v1.9.14), and with maxAge > 0 it does the same until the first
// rotation. So this is a minimal cumulative histogram on drogon's public Metric/Sample API, exported
// through the PromExporter plugin.
struct Hist : monitoring::Metric {
  std::vector<double> b;
  std::unique_ptr<std::atomic<uint64_t>[]> c;
  std::atomic<uint64_t> n{0};
  std::atomic<double> sum{0};
  Hist(const std::string &name, const std::string &route, std::vector<double> bounds)
      : Metric(name, {"route"}, {route}), b(std::move(bounds)), c(new std::atomic<uint64_t>[b.size() + 1]()) {}
  void observe(double v) {
    size_t i = 0; while (i < b.size() && v > b[i]) ++i;
    c[i].fetch_add(1, std::memory_order_relaxed); n.fetch_add(1, std::memory_order_relaxed);
    double s = sum.load(std::memory_order_relaxed); while (!sum.compare_exchange_weak(s, s + v, std::memory_order_relaxed)) {}
  }
  std::vector<monitoring::Sample> collect() const override {
    std::vector<monitoring::Sample> out; uint64_t acc = 0;
    for (size_t i = 0; i <= b.size(); ++i) {
      acc += c[i].load(); monitoring::Sample sm; sm.name = name_ + "_bucket";
      sm.exLabels.emplace_back("le", i < b.size() ? std::format("{}", b[i]) : "+Inf"); sm.value = (double)acc; out.push_back(std::move(sm));
    }
    monitoring::Sample s1; s1.name = name_ + "_sum"; s1.value = sum.load(); out.push_back(std::move(s1));
    monitoring::Sample s2; s2.name = name_ + "_count"; s2.value = (double)n.load(); out.push_back(std::move(s2));
    return out;
  }
};

// A fixed set of per-route histograms exported through PromExporter.
struct RouteHistograms : monitoring::CollectorBase {
  std::string name_ = "http_request_duration_seconds", help_ = "request duration";
  std::map<std::string, std::shared_ptr<Hist>> by_route;
  RouteHistograms() {
    std::vector<double> b{0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10};
    for (auto r : {"/plaintext", "/json", "/echo", "/db", "/healthz", "/readyz", "/metrics", "other"})
      by_route[r] = std::make_shared<Hist>(name_, r, b);
  }
  void observe(const std::string &path, double s) {
    auto it = by_route.find(path);
    (it == by_route.end() ? by_route.at("other") : it->second)->observe(s);
  }
  std::vector<monitoring::SamplesGroup> collect() const override {
    std::vector<monitoring::SamplesGroup> out;
    for (auto &[k, h] : by_route) out.push_back({h, h->collect()});
    return out;
  }
  const std::string &name() const override { return name_; }
  const std::string &help() const override { return help_; }
  const std::string_view type() const override { return "histogram"; }
};

static bool valid_rid(const std::string &s) {
  if (s.empty() || s.size() > 128) return false;
  for (char c : s) if (!(isalnum((unsigned char)c) || c == '.' || c == '_' || c == '-')) return false;
  return true;
}

int main() {
  const char *dbh = getenv("DB_HOST");
  auto hist = std::make_shared<RouteHistograms>();
  Json::Value prom; prom["name"] = "drogon::plugin::PromExporter"; prom["config"]["path"] = "/metrics";
  Json::Value plugins(Json::arrayValue); plugins.append(prom);
  Json::Value cfg; cfg["plugins"] = plugins; app().loadConfigJson(cfg);
  app().addListener("0.0.0.0", 8000)
      .setThreadNum(0)  // one IO loop per core
      .setIdleConnectionTimeout(75)
      .setClientMaxBodySize(1 << 20)
      .createDbClient("postgresql", dbh ? dbh : "127.0.0.1", 5432, "bench", "bench", "bench",
                      4 /* connections per IO loop: 4 loops x 4 = 16 */, "", "default", true /* fast */, "utf8", 10.0 /* timeout s */);
  app().registerBeginningAdvice([hist] {
    app().getPlugin<plugin::PromExporter>()->registerCollector(hist);
  });
  app().registerPreRoutingAdvice([](const HttpRequestPtr &req) {
    auto rid = req->getHeader("x-request-id");
    if (!valid_rid(rid)) rid = utils::getUuid();
    req->attributes()->insert("rid", rid);
    req->attributes()->insert("t0", Clock::now());
  });
  app().registerPreSendingAdvice([hist](const HttpRequestPtr &req, const HttpResponsePtr &resp) {
    auto attrs = req->attributes();
    if (!attrs->find("t0")) return;
    auto rid = attrs->get<std::string>("rid");
    double ms = std::chrono::duration<double, std::milli>(Clock::now() - attrs->get<Clock::time_point>("t0")).count();
    resp->addHeader("x-request-id", rid);
    hist->observe(req->path(), ms / 1e3);
    auto line = std::format("{{\"ts\":\"{}\",\"level\":\"info\",\"msg\":\"request\",\"method\":\"{}\",\"path\":\"{}\",\"status\":{},\"duration_ms\":{:.3f},\"request_id\":\"{}\"}}\n",
                            trantor::Date::now().toFormattedString(true), req->methodString(), req->path(), (int)resp->statusCode(), ms, rid);
    fwrite(line.data(), 1, line.size(), stdout);  // stdout is fully buffered (setvbuf below)
  });
  app().registerHandler("/plaintext", [](const HttpRequestPtr &, std::function<void(const HttpResponsePtr &)> &&cb) {
    auto r = HttpResponse::newHttpResponse(); r->setBody("Hello, World!"); r->setContentTypeCode(CT_TEXT_PLAIN); cb(r);
  }, {Get});
  app().registerHandler("/json", [](const HttpRequestPtr &, std::function<void(const HttpResponsePtr &)> &&cb) {
    Json::Value v; v["message"] = "Hello, World!"; cb(HttpResponse::newHttpJsonResponse(v));
  }, {Get});
  app().registerHandler("/echo", [](const HttpRequestPtr &req, std::function<void(const HttpResponsePtr &)> &&cb) {
    auto json = req->getJsonObject();
    if (!json) { auto r = HttpResponse::newHttpResponse(); r->setStatusCode(k400BadRequest); r->setBody("malformed json"); cb(r); return; }
    cb(HttpResponse::newHttpJsonResponse(*json));
  }, {Post});
  app().registerHandler("/db", [](const HttpRequestPtr &, std::function<void(const HttpResponsePtr &)> &&cb) {
    auto db = app().getFastDbClient();
    db->execSqlAsync("select id, randomnumber from world where id = $1",
      [cb](const orm::Result &r) {
        Json::Value v; v["id"] = r[0][0].as<int>(); v["randomNumber"] = r[0][1].as<int>();
        cb(HttpResponse::newHttpJsonResponse(v));
      },
      [cb](const orm::DrogonDbException &) { auto r = HttpResponse::newHttpResponse(); r->setStatusCode(k503ServiceUnavailable); cb(r); },
      random_id());
  }, {Get});
  app().registerHandler("/healthz", [](const HttpRequestPtr &, std::function<void(const HttpResponsePtr &)> &&cb) {
    auto r = HttpResponse::newHttpResponse(); r->setBody("ok"); cb(r);
  }, {Get});
  app().registerHandler("/readyz", [](const HttpRequestPtr &, std::function<void(const HttpResponsePtr &)> &&cb) {
    app().getFastDbClient()->execSqlAsync("select 1",
      [cb](const orm::Result &) { auto r = HttpResponse::newHttpResponse(); r->setBody("ready"); cb(r); },
      [cb](const orm::DrogonDbException &) { auto r = HttpResponse::newHttpResponse(); r->setStatusCode(k503ServiceUnavailable); cb(r); });
  }, {Get});
  static char buf[1 << 16]; setvbuf(stdout, buf, _IOFBF, sizeof buf);
  app().getLoop()->runEvery(0.5, [] { fflush(stdout); });
  app().run();
}
