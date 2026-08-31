package dev.obsion.sdk;

import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * REST client for the Obsion Python control plane.
 *
 * <p>This is not a second Harness, App Server, or control-plane runtime. Java teams
 * create Agent/Skill documents through Studio routes and Connectors through admin
 * routes. Capability execution still enters the Python Capability Gateway.
 */
public final class ObsionClient implements AutoCloseable {
  private final String baseUrl;
  private final String token;
  private final HttpClient http;

  public ObsionClient(String baseUrl, String token) {
    this(baseUrl, token, Duration.ofSeconds(60));
  }

  public ObsionClient(String baseUrl, String token, Duration timeout) {
    this.baseUrl = Objects.requireNonNull(baseUrl, "baseUrl").replaceAll("/+$", "");
    this.token = token;
    this.http =
        HttpClient.newBuilder()
            .connectTimeout(Objects.requireNonNull(timeout, "timeout"))
            .followRedirects(HttpClient.Redirect.NEVER)
            .build();
  }

  public List<Object> listWorkspaces() {
    return Json.asArray(request("GET", "/api/v1/workspaces", null));
  }

  public Map<String, Object> createWorkspace(String name) {
    return createWorkspace(name, "");
  }

  public Map<String, Object> createWorkspace(String name, String description) {
    return Json.asObject(
        request("POST", "/api/v1/workspaces", Map.of("name", name, "description", description)));
  }

  public Map<String, Object> createThread(String workspaceId, String title) {
    return Json.asObject(
        request(
            "POST",
            "/api/v1/threads",
            Map.of("workspace_id", workspaceId, "title", title)));
  }

  public Map<String, Object> createTurn(String threadId, String input) {
    return Json.asObject(
        request("POST", "/api/v1/threads/" + encode(threadId) + "/turns", Map.of("input", input)));
  }

  public Map<String, Object> listStudioCatalog() {
    return Json.asObject(request("GET", "/api/v1/studio/catalog", null));
  }

  public Map<String, Object> validateStudioDocument(String document) {
    return Json.asObject(
        request("POST", "/api/v1/studio/validate", Map.of("document", document)));
  }

  public Map<String, Object> publishStudioAgent(String document) {
    return Json.asObject(request("POST", "/api/v1/studio/agents", Map.of("document", document)));
  }

  public Map<String, Object> publishStudioSkill(String document) {
    return Json.asObject(request("POST", "/api/v1/studio/skills", Map.of("document", document)));
  }

  public Map<String, Object> promoteStudioVersion(String kind, String name, int version) {
    Map<String, Object> body = new LinkedHashMap<>();
    body.put("kind", kind);
    body.put("name", name);
    body.put("version", version);
    return Json.asObject(request("POST", "/api/v1/studio/promote", body));
  }

  public List<Object> listConnectors() {
    return Json.asArray(request("GET", "/api/v1/admin/connectors", null));
  }

  public Map<String, Object> createConnector(Map<String, Object> definition) {
    return Json.asObject(request("POST", "/api/v1/admin/connectors", definition));
  }

  public List<Object> listAdminCapabilities() {
    return Json.asArray(request("GET", "/api/v1/admin/capabilities", null));
  }

  public List<Object> listOperatorInvocations() {
    return listOperatorInvocations(null, 100);
  }

  public List<Object> listOperatorInvocations(String status, int limit) {
    if (limit < 1 || limit > 1000) {
      throw new IllegalArgumentException("limit must be between 1 and 1000");
    }
    StringBuilder path =
        new StringBuilder("/api/v1/admin/operator-invocations?limit=").append(limit);
    if (status != null && !status.isBlank()) {
      path.append("&status=").append(encode(status));
    }
    return Json.asArray(request("GET", path.toString(), null));
  }

  public Map<String, Object> bindCapability(
      String capabilityId, String connectorId, String environment) {
    Map<String, Object> body = new LinkedHashMap<>();
    body.put("connector_id", connectorId);
    body.put("environment", environment);
    body.put("resource_selector", Map.of());
    return Json.asObject(
        request(
            "POST",
            "/api/v1/admin/capabilities/" + encode(capabilityId) + "/bindings",
            body));
  }

  public List<Object> listCapabilities() {
    return Json.asArray(request("GET", "/api/v1/capabilities", null));
  }

  public Map<String, Object> invokeCapability(
      String capabilityName, Map<String, Object> invokeRequest) {
    return Json.asObject(
        request(
            "POST",
            "/api/v1/capabilities/" + encode(capabilityName) + "/invoke",
            invokeRequest));
  }

  private Object request(String method, String path, Map<String, Object> body) {
    try {
      HttpRequest.Builder builder =
          HttpRequest.newBuilder(URI.create(baseUrl + path))
              .timeout(Duration.ofSeconds(60))
              .header("Accept", "application/json");
      if (token != null && !token.isBlank()) {
        builder.header("Authorization", "Bearer " + token);
      }
      if (body == null) {
        builder.method(method, HttpRequest.BodyPublishers.noBody());
      } else {
        builder.header("Content-Type", "application/json");
        builder.method(method, HttpRequest.BodyPublishers.ofString(Json.stringify(body)));
      }
      HttpResponse<String> response = http.send(builder.build(), HttpResponse.BodyHandlers.ofString());
      String requestId = response.headers().firstValue("X-Request-ID").orElse("");
      if (response.statusCode() < 200 || response.statusCode() >= 300) {
        throw ObsionApiException.fromBody(response.statusCode(), response.body(), requestId);
      }
      String payload = response.body();
      if (payload == null || payload.isBlank()) {
        return Map.of();
      }
      return Json.parse(payload);
    } catch (ObsionApiException error) {
      throw error;
    } catch (InterruptedException error) {
      Thread.currentThread().interrupt();
      throw new ObsionApiException(0, "http_error", error.getMessage(), "");
    } catch (IOException error) {
      throw new ObsionApiException(0, "http_error", error.getMessage(), "");
    }
  }

  private static String encode(String value) {
    return URLEncoder.encode(value, StandardCharsets.UTF_8).replace("+", "%20");
  }

  @Override
  public void close() {
    if (http instanceof AutoCloseable closeable) {
      try {
        closeable.close();
      } catch (Exception ignored) {
        // HttpClient is AutoCloseable after Java 21; Java 21 itself has no close().
      }
    }
  }
}
