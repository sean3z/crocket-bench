// TFB plaintext + json + db on crocket 0.5.2, production track: Logger (JSON access log), Metrics (/metrics),
// request timeout 10s, built-in /healthz + /readyz (pool select 1), built-in x-request-id. Nothing added.
#include <crocket/crocket.hpp>
#include <crocket/db.hpp>

#include <cstdint>
#include <optional>
#include <random>
#include <string>

using namespace crocket;

struct Message { std::string message; };

struct Item { std::string name; std::int32_t qty; };

struct World {
  std::int32_t id;
  [[= json::rename("randomNumber")]] std::int32_t randomnumber;
};
template <> constexpr auto crocket::db::table_name<World> = "world";

static auto random_id() -> std::int32_t {
  thread_local std::mt19937 rng{std::random_device{}()};
  thread_local std::uniform_int_distribution<std::int32_t> dist{1, 10000};
  return dist(rng);
}

namespace api {

[[= http::get("/plaintext")]]
auto plaintext() -> std::string { return "Hello, World!"; }

[[= http::get("/json")]]
auto json_() -> Message { return {"Hello, World!"}; }

[[= http::post("/echo")]]
auto echo(Json<Item> body) -> Json<Item> { return body; }

[[= http::get("/db")]]
auto db_(Db& db) -> std::optional<World> { return db.find<World>(random_id()); }

}  // namespace api

int main() {
  auto cfg = Config::from_env();
  db::Pool pool(cfg.database);
  return build(cfg)
      .manage(std::move(pool))
      .attach(Logger{})
      .attach(Metrics{})
      .mount("/", reflect_routes<^^api>())
      .launch();
}
