package dev.obsion.sdk;

import java.util.Map;
import java.util.Objects;

/** Structured Obsion REST error. This client does not interpret Harness internals. */
public final class ObsionApiException extends RuntimeException {
  private final int statusCode;
  private final String code;
  private final String correlationId;

  public ObsionApiException(int statusCode, String code, String message, String correlationId) {
    super(message);
    this.statusCode = statusCode;
    this.code = Objects.requireNonNullElse(code, "http_error");
    this.correlationId = Objects.requireNonNullElse(correlationId, "");
  }

  public int statusCode() {
    return statusCode;
  }

  public String code() {
    return code;
  }

  public String correlationId() {
    return correlationId;
  }

  @SuppressWarnings("unchecked")
  static ObsionApiException fromBody(int statusCode, String raw, String requestId) {
    Object parsed;
    try {
      parsed = Json.parse(raw);
    } catch (RuntimeException ignored) {
      parsed = Map.of();
    }
    if (!(parsed instanceof Map<?, ?> map)) {
      return new ObsionApiException(statusCode, "http_error", "Obsion API request failed", requestId);
    }
    Map<String, Object> body = (Map<String, Object>) map;
    return new ObsionApiException(
        statusCode,
        string(body.get("code"), "http_error"),
        string(body.get("message"), "Obsion API request failed"),
        string(body.get("correlation_id"), requestId));
  }

  private static String string(Object value, String fallback) {
    return value == null ? fallback : String.valueOf(value);
  }
}
