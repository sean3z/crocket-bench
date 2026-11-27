FROM maven:3-eclipse-temurin-25 AS build
WORKDIR /src
COPY pom.xml ./
RUN mvn -q -B dependency:go-offline
COPY src ./src
RUN mvn -q -B package -DskipTests
FROM eclipse-temurin:25-jre
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/target/bench.jar /app/bench.jar
EXPOSE 8000
ENTRYPOINT ["sh", "-c", "exec java $JAVA_OPTS -jar /app/bench.jar"]
