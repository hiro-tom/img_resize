import { api, SftpSettings } from "./api";

async function demo() {
  const existing = await api.getSettings();
  console.log("Current settings", existing);

  // Example: update settings (replace with values from form inputs)
  const sample: SftpSettings = {
    host: "sftp.example.com",
    port: 22,
    username: "demo",
    password: "password",
    remote_dir: "/remote/path",
    local_dir: "C:/temp/sftp-client",
    interval_minutes: 60,
  };

  await api.saveSettings(sample);
  await api.triggerSync(true);
  const status = await api.getStatus();
  const logs = await api.getLogs(20);
  console.log({ status, logs });
}

demo().catch((err) => console.error(err));
