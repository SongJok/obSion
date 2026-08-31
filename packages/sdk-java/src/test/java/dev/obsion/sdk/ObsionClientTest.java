package dev.obsion.sdk;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.sun.net.httpserver.HttpServer;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executors;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

final class ObsionClientTest {
  private HttpServer server;
  private final List<Recorded> requests = new ArrayList<>();
  private String responseBody = "[]";
  private int status = 200;

  @BeforeEach
  void startServer() throws Exception {
    server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
    server.createContext(
        "/",
        exchange -> {
          byte[] body = exchange.getRequestBody().readAllBytes();
          requests.add(
              new Recorded(
                  exchange.getRequestMethod(),
                  exchange.getRequestURI().getPath(),
                  exchange.getRequestURI().getRawQuery(),
                  exchange.getRequestHeaders().getFirst("Authorization"),
                  body.length == 0 ? null : new String(body, StandardCharsets.UTF_8)));
          byte[] payload = responseBody.getBytes(StandardCharsets.UTF_8);
          exchange.getResponseHeaders().add("Content-Type", "application/json");
          exchange.sendResponseHeaders(status, payload.length);
          exchange.getResponseBody().write(payload);
          exchange.close();
        });
    server.setExecutor(Executors.newSingleThreadExecutor());
    server.start();
  }

  @AfterEach
  void stopServer() {
    server.stop(0);
  }

  @Test
  void listWorkspacesSendsBearerToken() {
    responseBody = "[{\"id\":\"workspace-1\"}]";
    try (ObsionClient client = new ObsionClient(baseUrl(), "token")) {
      List<Object> workspaces = client.listWorkspaces();
      assertEquals("workspace-1", Json.asObject(workspaces.get(0)).get("id"));
    }
    assertEquals("GET", requests.get(0).method);
    assertEquals("/api/v1/workspaces", requests.get(0).path);
    assertEquals("Bearer token", requests.get(0).authorization);
  }

  @Test
  void structuredErrorsPreserveCorrelationId() {
    status = 403;
    responseBody = "{\"code\":\"denied\",\"message\":\"Denied\",\"correlation_id\":\"request-1\"}";
    try (ObsionClient client = new ObsionClient(baseUrl(), null)) {
      ObsionApiException error =
          assertThrows(ObsionApiException.class, () -> client.listWorkspaces());
      assertEquals(403, error.statusCode());
      assertEquals("denied", error.code());
      assertEquals("request-1", error.correlationId());
    }
  }

  @Test
  void registryAndConnectorMethodsUseControlPlaneRoutes() {
    responseBody = "{\"id\":\"created\"}";
    try (ObsionClient client = new ObsionClient(baseUrl(), "token")) {
      client.publishStudioAgent("kind: Agent");
      client.publishStudioSkill("kind: Skill");
      Map<String, Object> connector = new LinkedHashMap<>();
      connector.put("name", "obsion-workflow-dispatch-test");
      connector.put("connector_type", "workflow-development");
      connector.put("environment", "development");
      connector.put("status", "ACTIVE");
      connector.put("declared_grants", List.of("automation.trigger"));
      connector.put("allowed_egress", List.of());
      client.createConnector(connector);
      client.bindCapability("capability-1", "connector-1", "development");
      Map<String, Object> invoke = new LinkedHashMap<>();
      invoke.put("run_id", "run-1");
      invoke.put("payload", Map.of("input", Map.of("ping", "pong")));
      invoke.put("resource", Map.of());
      invoke.put("environment", "development");
      client.invokeCapability("workflow.automation.trigger", invoke);
    }
    assertEquals("/api/v1/studio/agents", requests.get(0).path);
    assertEquals("/api/v1/studio/skills", requests.get(1).path);
    assertEquals("/api/v1/admin/connectors", requests.get(2).path);
    assertTrue(requests.get(2).body.contains("\"name\":\"obsion-workflow-dispatch-test\""));
    assertEquals("/api/v1/admin/capabilities/capability-1/bindings", requests.get(3).path);
    assertEquals("/api/v1/capabilities/workflow.automation.trigger/invoke", requests.get(4).path);
  }

  @Test
  void operatorInvocationProjectionUsesContentFreeAdminRoute() {
    responseBody = "[{\"status\":\"UNKNOWN\",\"reconciliation_required\":true}]";
    try (ObsionClient client = new ObsionClient(baseUrl(), "token")) {
      List<Object> records = client.listOperatorInvocations("UNKNOWN", 25);
      assertEquals("UNKNOWN", Json.asObject(records.get(0)).get("status"));
    }
    assertEquals("GET", requests.get(0).method);
    assertEquals("/api/v1/admin/operator-invocations", requests.get(0).path);
    assertEquals("limit=25&status=UNKNOWN", requests.get(0).query);
  }

  private String baseUrl() {
    return "http://127.0.0.1:" + server.getAddress().getPort();
  }

  private record Recorded(
      String method, String path, String query, String authorization, String body) {}
}
