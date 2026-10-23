import tokens from "../../../design/motion-tokens.json" with { type: "json" };

// Durations and stagger are milliseconds; distances are pixels. No motion runtime required.
export const motionTokens = tokens;

export const motionPresets = {
  enter: {
    duration: tokens.durationMs.normal,
    easing: tokens.easing.enter,
    distance: tokens.distancePx.medium,
    stagger: tokens.staggerMs.min,
  },
  exit: {
    duration: tokens.durationMs.fast,
    easing: tokens.easing.exit,
    distance: tokens.distancePx.small,
    stagger: tokens.staggerMs.min,
  },
};

export const reducedMotionPresets = {
  enter: {
    ...motionPresets.enter,
    duration: tokens.reducedMotion.durationMs,
    distance: tokens.reducedMotion.distancePx,
    stagger: tokens.reducedMotion.staggerMs,
  },
  exit: {
    ...motionPresets.exit,
    duration: tokens.reducedMotion.durationMs,
    distance: tokens.reducedMotion.distancePx,
    stagger: tokens.reducedMotion.staggerMs,
  },
};

// Pass matchMedia(motionTokens.reducedMotion.mediaQuery).matches from the UI.
export function getMotionPresets(reducedMotion = false) {
  return reducedMotion ? reducedMotionPresets : motionPresets;
}
