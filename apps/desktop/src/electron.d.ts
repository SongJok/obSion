declare module "electron" {
  export const app: {
    whenReady(): Promise<void>;
    on(event: "window-all-closed", listener: () => void): void;
    quit(): void;
  };
  export class BrowserWindow {
    constructor(options: {
      width?: number;
      height?: number;
      webPreferences?: {
        contextIsolation?: boolean;
        nodeIntegration?: boolean;
        sandbox?: boolean;
      };
    });
    loadURL(url: string): Promise<void>;
  }
}
