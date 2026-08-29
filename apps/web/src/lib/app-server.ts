import {
  ObsionAppServerClient,
  appServerUrlFromApiUrl,
  type AppServerNotification,
  type RunEvent as AppServerRunEvent,
} from "@obsion/sdk";

import { API_URL } from "./api";
import type { RunEvent } from "./types";

export async function streamRunEvents(
  runId: string,
  afterSequence: number,
  onEvent: (event: RunEvent) => void,
): Promise<() => void> {
  const client = new ObsionAppServerClient(appServerUrlFromApiUrl(API_URL), {
    clientName: "obsion-workbench",
    clientVersion: "0.1.0",
  });
  let subscriptionId: string | undefined;
  const stopListening = client.onNotification((notification) => {
    const event = eventFromNotification(notification);
    if (
      event &&
      event.run_id === runId &&
      (!subscriptionId || notification.params.subscription_id === subscriptionId)
    ) {
      onEvent(event);
    }
  });
  try {
    await client.connect();
    const subscription = await client.subscribeRun(runId, afterSequence);
    subscriptionId = subscription.subscription_id;
  } catch (error) {
    stopListening();
    client.close();
    throw error;
  }
  return () => {
    stopListening();
    client.close();
  };
}

function eventFromNotification(
  notification: AppServerNotification,
): RunEvent | undefined {
  const candidate = notification.params.event;
  if (!candidate || typeof candidate !== "object") return undefined;
  const event = candidate as Partial<AppServerRunEvent>;
  if (
    typeof event.id !== "string" ||
    typeof event.name !== "string" ||
    typeof event.sequence !== "number" ||
    typeof event.run_sequence !== "number" ||
    typeof event.created_at !== "string" ||
    !event.payload ||
    typeof event.payload !== "object"
  ) {
    return undefined;
  }
  return event as RunEvent;
}
