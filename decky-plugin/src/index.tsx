import { useState, useEffect, useCallback, FC } from "react";
import {
  PanelSection,
  PanelSectionRow,
  ToggleField,
  SliderField,
  DropdownItem,
  ButtonItem,
  staticClasses,
} from "@decky/ui";
import { callable, definePlugin } from "@decky/api";

// Backend RPC bindings
const getStatus = callable<[], {
  button_fix: { applied: boolean; error?: string };
  sleep_fix: { applied: boolean; all_kargs_set: boolean; udev_rule: boolean };
  home_button: boolean;
  fan: FanStatus;
}>("get_status");

const applyButtonFix = callable<[], { success: boolean; message?: string; error?: string }>("apply_button_fix");
const applySleepFix = callable<[], { success: boolean; reboot_needed?: boolean; error?: string }>("apply_sleep_fix");
const startHomeButton = callable<[], { running: boolean }>("start_home_button");
const stopHomeButton = callable<[], { running: boolean }>("stop_home_button");
const setFanMode = callable<[string], { success: boolean }>("set_fan_mode");
const setFanSpeed = callable<[number], { success: boolean }>("set_fan_speed");
const setFanProfile = callable<[string], { success: boolean }>("set_fan_profile");
const getFanStatus = callable<[], FanStatus>("get_fan_status");

interface FanStatus {
  available: boolean;
  rpm?: number;
  percent?: number;
  hw_mode?: string;
  temp?: number;
  mode?: string;
  profile?: string;
  speed?: number;
  backend?: string;
  error?: string;
}

const PROFILE_OPTIONS = [
  { data: "silent", label: "Silent" },
  { data: "balanced", label: "Balanced" },
  { data: "performance", label: "Performance" },
  { data: "custom", label: "Custom (slider)" },
];

const Content: FC = () => {
  const [buttonFix, setButtonFix] = useState<{ applied: boolean; error?: string }>({ applied: false });
  const [sleepFix, setSleepFix] = useState<{ applied: boolean; all_kargs_set: boolean; udev_rule: boolean }>({
    applied: false, all_kargs_set: false, udev_rule: false,
  });
  const [sleepReboot, setSleepReboot] = useState(false);
  const [homeButton, setHomeButton] = useState(false);
  const [fan, setFan] = useState<FanStatus>({ available: false });
  const [loading, setLoading] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const status = await getStatus();
      setButtonFix(status.button_fix);
      setSleepFix(status.sleep_fix);
      setHomeButton(status.home_button);
      setFan(status.fan);
    } catch (e) {
      console.error("Failed to get status:", e);
    }
  }, []);

  // Initial load + periodic fan status refresh
  useEffect(() => {
    refresh();
    const interval = setInterval(async () => {
      if (fan.available && fan.mode === "manual") {
        try {
          setFan(await getFanStatus());
        } catch { /* ignore */ }
      }
    }, 2000);
    return () => clearInterval(interval);
  }, [fan.available, fan.mode]);

  const handleButtonFix = async (enabled: boolean) => {
    if (!enabled) return; // Toggle on only — no revert
    setLoading("button");
    try {
      const result = await applyButtonFix();
      if (result.success) {
        setButtonFix({ applied: true });
      }
    } finally {
      setLoading(null);
      refresh();
    }
  };

  const handleSleepFix = async (enabled: boolean) => {
    if (!enabled) return;
    setLoading("sleep");
    try {
      const result = await applySleepFix();
      if (result.success) {
        setSleepFix({ applied: true, all_kargs_set: true, udev_rule: true });
        if (result.reboot_needed) {
          setSleepReboot(true);
        }
      }
    } finally {
      setLoading(null);
      refresh();
    }
  };

  const handleHomeButton = async (enabled: boolean) => {
    setLoading("home");
    try {
      if (enabled) {
        await startHomeButton();
        setHomeButton(true);
      } else {
        await stopHomeButton();
        setHomeButton(false);
      }
    } finally {
      setLoading(null);
    }
  };

  const handleFanMode = async (manual: boolean) => {
    setLoading("fan");
    try {
      await setFanMode(manual ? "manual" : "auto");
      setFan((prev) => ({ ...prev, mode: manual ? "manual" : "auto" }));
    } finally {
      setLoading(null);
      refresh();
    }
  };

  const handleFanSpeed = async (value: number) => {
    await setFanSpeed(value);
    setFan((prev) => ({ ...prev, speed: value, profile: "custom" }));
  };

  const handleFanProfile = async (profile: string) => {
    setLoading("profile");
    try {
      await setFanProfile(profile);
      setFan((prev) => ({ ...prev, profile }));
    } finally {
      setLoading(null);
      refresh();
    }
  };

  return (
    <>
      {/* Warning Banner */}
      <PanelSection>
        <PanelSectionRow>
          <div style={{
            backgroundColor: "#4a3000",
            border: "1px solid #7a5000",
            borderRadius: "4px",
            padding: "8px 12px",
            fontSize: "12px",
            lineHeight: "1.4",
            color: "#ffcc00",
          }}>
            Temporary workaround for Bazzite on OneXPlayer Apex.
            Fixes (buttons, sleep) will not persist across Bazzite updates
            and must be re-applied.
          </div>
        </PanelSectionRow>
      </PanelSection>

      {/* Fixes Section */}
      <PanelSection title="Fixes">
        <PanelSectionRow>
          <ToggleField
            label="Button Fix"
            description={
              buttonFix.applied
                ? "Applied"
                : buttonFix.error
                  ? `Error: ${buttonFix.error}`
                  : "Not applied"
            }
            checked={buttonFix.applied}
            disabled={buttonFix.applied || loading === "button"}
            onChange={handleButtonFix}
          />
        </PanelSectionRow>

        <PanelSectionRow>
          <ToggleField
            label="Sleep Fix"
            description={
              sleepFix.applied
                ? sleepReboot
                  ? "Applied — Reboot required"
                  : "Applied"
                : "Not applied"
            }
            checked={sleepFix.applied}
            disabled={sleepFix.applied || loading === "sleep"}
            onChange={handleSleepFix}
          />
        </PanelSectionRow>

        <PanelSectionRow>
          <ToggleField
            label="Home Button → HHD"
            description={homeButton ? "Running" : "Stopped"}
            checked={homeButton}
            disabled={loading === "home"}
            onChange={handleHomeButton}
          />
        </PanelSectionRow>
      </PanelSection>

      {/* Fan Control Section */}
      <PanelSection title="Fan Control">
        {!fan.available ? (
          <PanelSectionRow>
            <div style={{ fontSize: "12px", color: "#888" }}>
              {fan.error || "Fan control not available"}
            </div>
          </PanelSectionRow>
        ) : (
          <>
            {/* Status line */}
            <PanelSectionRow>
              <div style={{ fontSize: "12px", color: "#aaa" }}>
                {fan.temp != null && `${fan.temp}°C`}
                {fan.rpm != null && ` · ${fan.rpm} RPM`}
                {fan.percent != null && ` · ${Math.round(fan.percent)}%`}
              </div>
            </PanelSectionRow>

            <PanelSectionRow>
              <ToggleField
                label="Manual Fan Control"
                checked={fan.mode === "manual"}
                disabled={loading === "fan"}
                onChange={handleFanMode}
              />
            </PanelSectionRow>

            {fan.mode === "manual" && (
              <>
                <PanelSectionRow>
                  <DropdownItem
                    label="Fan Profile"
                    rgOptions={PROFILE_OPTIONS.map((o) => ({
                      data: o.data,
                      label: o.label,
                    }))}
                    selectedOption={fan.profile || "custom"}
                    onChange={(option) => handleFanProfile(option.data)}
                  />
                </PanelSectionRow>

                {fan.profile === "custom" && (
                  <PanelSectionRow>
                    <SliderField
                      label="Fan Speed"
                      value={fan.speed ?? 50}
                      min={0}
                      max={100}
                      step={5}
                      showValue
                      onChange={handleFanSpeed}
                    />
                  </PanelSectionRow>
                )}
              </>
            )}
          </>
        )}
      </PanelSection>
    </>
  );
};

export default definePlugin(() => ({
  name: "OneXPlayer Apex Tools",
  titleView: (
    <div className={staticClasses.Title}>OXP Apex Tools</div>
  ),
  content: <Content />,
  icon: (
    <svg viewBox="0 0 24 24" fill="currentColor" width="20" height="20">
      <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z" />
    </svg>
  ),
}));
