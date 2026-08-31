export interface DesktopHost {
  configuration(): Record<string, unknown>;
  environment(): Record<string, string | undefined>;
  getSecret(key: string): Promise<string | undefined>;
  storeSecret(key: string, value: string): Promise<void>;
  deleteSecret(key: string): Promise<void>;
  showInput(options: {
    prompt: string;
    placeholder?: string;
    password?: boolean;
  }): Promise<string | undefined>;
  appendOutput(text: string): void;
  showError(message: string): void;
}
