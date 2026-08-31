import { app, BrowserWindow } from "electron";

function loopbackUrl(value: string | undefined): string {
  if (!value) {
    throw new Error("obsion-desktop electron host requires a loopback URL");
  }
  const parsed = new URL(value);
  if (parsed.protocol !== "http:" || parsed.hostname !== "127.0.0.1") {
    throw new Error("Desktop window may only load http://127.0.0.1");
  }
  return parsed.toString();
}

const target = loopbackUrl(process.argv[2]);

void app.whenReady().then(async () => {
  const window = new BrowserWindow({
    width: 1100,
    height: 760,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  await window.loadURL(target);
});

app.on("window-all-closed", () => {
  app.quit();
});
