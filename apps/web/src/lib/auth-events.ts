export const AUTHENTICATION_REQUIRED_EVENT = "obsion:authentication-required";

export function notifyAuthenticationRequired() {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(AUTHENTICATION_REQUIRED_EVENT));
  }
}
