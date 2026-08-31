package dev.obsion.sdk;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

final class JsonTest {
  @Test
  void roundTripsMapsArraysAndUnicode() {
    Map<String, Object> payload =
        Map.of(
            "name", "obsion-workflow-dispatch-test",
            "grants", List.of("automation.trigger"),
            "nested", Map.of("ok", true, "count", 2));
    Object parsed = Json.parse(Json.stringify(payload));
    Map<String, Object> object = Json.asObject(parsed);
    assertEquals("obsion-workflow-dispatch-test", object.get("name"));
    assertEquals("automation.trigger", Json.asArray(object.get("grants")).get(0));
    assertEquals(true, Json.asObject(object.get("nested")).get("ok"));
  }

  @Test
  void parsesErrorBodies() {
    Map<String, Object> body =
        Json.asObject(Json.parse("{\"code\":\"denied\",\"message\":\"Denied\",\"correlation_id\":\"request-1\"}"));
    assertEquals("denied", body.get("code"));
    assertEquals("request-1", body.get("correlation_id"));
  }
}
