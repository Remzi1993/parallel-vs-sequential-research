# Pinned multi-platform indexes: build and runtime stay fixed until deliberately updated.
FROM maven:3.9.16-eclipse-temurin-25@sha256:93b8a14ea2f412782e4e842651273b4d903e35cc496284f178fbbe2d67d00976 AS build
WORKDIR /build
COPY pom.xml .
COPY src ./src
# Tests are part of every uncached build; Maven is not present in the runtime stage.
RUN mvn --batch-mode --no-transfer-progress verify

FROM eclipse-temurin:25-jdk-noble@sha256:80f67fb3d6f76e2d817d98a4cc2a9cf05f054d4e6adc5e2144bde671d722e63f
WORKDIR /app
COPY --from=build /build/target/benchmarks.jar /app/benchmarks.jar
COPY entrypoint.sh /app/entrypoint.sh
# Explicit sh invocation works even when checkout executable bits are unavailable.
ENTRYPOINT ["sh", "/app/entrypoint.sh"]
