import { FC } from "react";
import {
  ButtonItem,
  PanelSection,
  PanelSectionRow,
  Spinner,
  ToggleField,
} from "@decky/ui";
import type { LoadingState, ResultMessage, OxpecStatus, ResumeFixStatus, SleepEnableStatus } from "./types";
import {
  applyButtonFix, revertButtonFix, setInterceptMode, removeSleepFix,
  applyOxpec, revertOxpec,
  applyResumeFix, revertResumeFix,
  applySleepEnable, revertSleepEnable,
} from "./rpc";
import { InlineStatus } from "./InlineStatus";

export const FixesSection: FC<{
  buttonFix: { applied: boolean; error?: string; home_monitor_running?: boolean; intercept_enabled?: boolean };
  setButtonFix: React.Dispatch<React.SetStateAction<{ applied: boolean; error?: string; home_monitor_running?: boolean; intercept_enabled?: boolean }>>;
  sleepFix: { has_kargs: boolean; kargs_found: string[] };
  oxpec: OxpecStatus;
  setOxpec: React.Dispatch<React.SetStateAction<OxpecStatus>>;
  resumeFix: ResumeFixStatus;
  setResumeFix: React.Dispatch<React.SetStateAction<ResumeFixStatus>>;
  sleepEnable: SleepEnableStatus;
  setSleepEnable: React.Dispatch<React.SetStateAction<SleepEnableStatus>>;
  loading: LoadingState;
  setLoading: (l: LoadingState) => void;
  showResult: (key: string, text: string, type: "success" | "error") => void;
  result: ResultMessage | null;
  statusLoaded: boolean;
  refresh: () => Promise<void>;
}> = ({ buttonFix, setButtonFix, sleepFix, oxpec, setOxpec, resumeFix, setResumeFix, sleepEnable, setSleepEnable, loading, setLoading, showResult, result, statusLoaded, refresh }) => {
  const handleButtonFix = async (enabled: boolean) => {
    setLoading({
      active: "button",
      message: enabled
        ? "Applying button fix... (may take up to 60s for filesystem unlock)"
        : "Reverting button fix...",
    });
    try {
      const res = enabled ? await applyButtonFix() : await revertButtonFix();
      if (res.success) {
        setButtonFix({ applied: enabled });
        showResult("button", res.message || (enabled ? "Applied" : "Reverted"), "success");
      } else {
        showResult("button", res.error || "Failed", "error");
      }
    } catch (e) {
      showResult("button", `Error: ${e}`, "error");
    } finally {
      setLoading({ active: null, message: "" });
      refresh();
    }
  };

  const handleInterceptMode = async (fullIntercept: boolean) => {
    setLoading({ active: "intercept", message: "Switching controller mode..." });
    try {
      const res = await setInterceptMode(fullIntercept);
      if (res.success) {
        setButtonFix((prev) => ({ ...prev, intercept_enabled: fullIntercept }));
        showResult("intercept", res.message || (fullIntercept ? "Full intercept" : "Face buttons only"), "success");
      } else {
        showResult("intercept", res.error || "Failed", "error");
      }
    } catch (e) {
      showResult("intercept", `Error: ${e}`, "error");
    } finally {
      setLoading({ active: null, message: "" });
      refresh();
    }
  };

  const handleRemoveSleepFix = async () => {
    setLoading({ active: "sleep", message: "Removing sleep fix kargs (rpm-ostree)..." });
    try {
      const res = await removeSleepFix();
      if (res.success) {
        if (res.reboot_needed) {
          showResult("sleep", "Removed — reboot required. Re-apply button fix after reboot.", "success");
        } else {
          showResult("sleep", res.message || "No kargs to remove", "success");
        }
      } else {
        showResult("sleep", res.error || "Failed", "error");
      }
    } catch (e) {
      showResult("sleep", `Error: ${e}`, "error");
    } finally {
      setLoading({ active: null, message: "" });
      refresh();
    }
  };

  const handleOxpec = async (enabled: boolean) => {
    setLoading({
      active: "oxpec",
      message: enabled ? "Installing oxpec driver..." : "Removing oxpec driver...",
    });
    try {
      const res = enabled ? await applyOxpec() : await revertOxpec();
      if (res.success) {
        setOxpec((prev) => ({ ...prev, applied: enabled }));
        showResult("oxpec", res.message || (enabled ? "Installed" : "Removed"), "success");
      } else {
        showResult("oxpec", res.error || "Failed", "error");
      }
    } catch (e) {
      showResult("oxpec", `Error: ${e}`, "error");
    } finally {
      setLoading({ active: null, message: "" });
      refresh();
    }
  };

  const handleResumeFix = async (enabled: boolean) => {
    setLoading({
      active: "resume",
      message: enabled ? "Installing resume recovery..." : "Removing resume recovery...",
    });
    try {
      const res = enabled ? await applyResumeFix() : await revertResumeFix();
      if (res.success) {
        setResumeFix((prev) => ({ ...prev, applied: enabled }));
        showResult("resume", res.message || (enabled ? "Installed" : "Removed"), "success");
      } else {
        showResult("resume", res.error || "Failed", "error");
      }
    } catch (e) {
      showResult("resume", `Error: ${e}`, "error");
    } finally {
      setLoading({ active: null, message: "" });
      refresh();
    }
  };

  const handleSleepEnable = async (enabled: boolean) => {
    setLoading({
      active: "sleepEnable",
      message: enabled ? "Applying sleep fix..." : "Reverting sleep fix...",
    });
    try {
      const res = enabled ? await applySleepEnable() : await revertSleepEnable();
      if (res.success) {
        setSleepEnable((prev) => ({ ...prev, applied: enabled }));
        showResult("sleepEnable", res.message || (enabled ? "Applied" : "Reverted"), "success");
      } else {
        showResult("sleepEnable", res.error || "Failed", "error");
      }
    } catch (e) {
      showResult("sleepEnable", `Error: ${e}`, "error");
    } finally {
      setLoading({ active: null, message: "" });
      refresh();
    }
  };

  return (
    <PanelSection title="Fixes">
      {!statusLoaded ? (
        <PanelSectionRow>
          <div style={{ display: "flex", alignItems: "center", gap: "8px", padding: "8px 0" }}>
            <Spinner style={{ width: "16px", height: "16px" }} />
            <span style={{ fontSize: "12px", color: "#aaa" }}>Loading status...</span>
          </div>
        </PanelSectionRow>
      ) : (
        <>
          {/* EC Sensor Driver (oxpec) */}
          <PanelSectionRow>
            <ToggleField
              label="EC Sensor Driver (oxpec)"
              description={
                oxpec.applied
                  ? `Loaded${oxpec.hwmon_path ? ` · hwmon active` : ""}`
                  : oxpec.error && oxpec.error !== "module not loaded"
                    ? `Error: ${oxpec.error}`
                    : "Enables HHD fan curves & hwmon sensors"
              }
              checked={oxpec.applied}
              disabled={loading.active === "oxpec"}
              onChange={handleOxpec}
            />
          </PanelSectionRow>
          <InlineStatus loading={loading} result={result} section="oxpec" />
          {oxpec.kernel_compatible === false && (
            <PanelSectionRow>
              <div
                style={{
                  backgroundColor: "#4a3000",
                  border: "1px solid #7a5000",
                  borderRadius: "4px",
                  padding: "8px 12px",
                  fontSize: "11px",
                  lineHeight: "1.4",
                  color: "#ffcc00",
                }}
              >
                Kernel mismatch: module built for <strong>{oxpec.target_kernel}</strong>,
                running <strong>{oxpec.running_kernel}</strong>. Module may fail to load.
              </div>
            </PanelSectionRow>
          )}

          {/* Button Fix */}
          <PanelSectionRow>
            <ToggleField
              label="Button Fix"
              description={
                buttonFix.applied
                  ? `Applied${buttonFix.home_monitor_running ? " · Home active" : ""} (toggle off to revert)`
                  : buttonFix.error
                    ? `Error: ${buttonFix.error}`
                    : "Not applied"
              }
              checked={buttonFix.applied}
              disabled={loading.active === "button"}
              onChange={handleButtonFix}
            />
          </PanelSectionRow>
          <InlineStatus loading={loading} result={result} section="button" />
          {buttonFix.applied && (
            <>
              <PanelSectionRow>
                <ToggleField
                  label="Back Paddle Support"
                  description={
                    buttonFix.intercept_enabled !== false
                      ? "ON — L4/R4 back paddles enabled"
                      : "OFF — Standard gamepad mode"
                  }
                  checked={buttonFix.intercept_enabled !== false}
                  disabled={loading.active === "intercept"}
                  onChange={handleInterceptMode}
                />
              </PanelSectionRow>
              <InlineStatus loading={loading} result={result} section="intercept" />
              <PanelSectionRow>
                <div
                  style={{
                    backgroundColor: "#1a2a3a",
                    border: "1px solid #2a4a6a",
                    borderRadius: "4px",
                    padding: "8px 12px",
                    fontSize: "11px",
                    lineHeight: "1.4",
                    color: "#88bbdd",
                  }}
                >
                  {buttonFix.intercept_enabled !== false
                    ? "Back paddles (L4/R4) work as extra buttons. You can remap them in Steam Input settings (per-game or global). If you experience stick drift or input issues, switch this off."
                    : "Standard mode — Home and QAM buttons work, all other input handled by the default gamepad driver. Turn this on to enable L4/R4 back paddles."}
                </div>
              </PanelSectionRow>
            </>
          )}

          {/* Resume Recovery */}
          <PanelSectionRow>
            <ToggleField
              label="Resume Recovery"
              description={
                resumeFix.applied
                  ? "Active — gamepad recovers after sleep"
                  : "Fix gamepad not working after sleep"
              }
              checked={resumeFix.applied}
              disabled={loading.active === "resume"}
              onChange={handleResumeFix}
            />
          </PanelSectionRow>
          <InlineStatus loading={loading} result={result} section="resume" />

          {/* Sleep Enable (fan noise + fingerprint wake) */}
          <PanelSectionRow>
            <ToggleField
              label="Sleep Fix (Fan Noise)"
              description={
                sleepEnable.applied
                  ? "Applied — fans stop during sleep"
                  : "Fix fans running during sleep"
              }
              checked={sleepEnable.applied}
              disabled={loading.active === "sleepEnable"}
              onChange={handleSleepEnable}
            />
          </PanelSectionRow>
          <InlineStatus loading={loading} result={result} section="sleepEnable" />

          {/* Legacy sleep fix kargs cleanup */}
          {sleepFix.has_kargs && (
            <>
              <PanelSectionRow>
                <div
                  style={{
                    backgroundColor: "#4a3000",
                    border: "1px solid #7a5000",
                    borderRadius: "4px",
                    padding: "8px 12px",
                    fontSize: "11px",
                    lineHeight: "1.4",
                    color: "#ffcc00",
                  }}
                >
                  Previous sleep fix kargs detected: <strong>{sleepFix.kargs_found.join(", ")}</strong>.
                  These don't work on Strix Halo (kernel 6.17) and may cause hangs on sleep.
                  Remove them to restore default behavior.
                </div>
              </PanelSectionRow>
              <PanelSectionRow>
                <ButtonItem
                  layout="below"
                  onClick={handleRemoveSleepFix}
                  disabled={loading.active === "sleep"}
                >
                  Remove Sleep Fix Kargs
                </ButtonItem>
              </PanelSectionRow>
              <InlineStatus loading={loading} result={result} section="sleep" />
            </>
          )}
        </>
      )}
    </PanelSection>
  );
};
