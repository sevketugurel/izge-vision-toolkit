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

type MotionPreset = typeof motionPresets.enter;

// motion/react takes seconds and a cubic-bezier control-point tuple; convert from the token units.
function parseBezier(easing: string): [number, number, number, number] {
  const [a, b, c, d] = easing
    .replace(/^cubic-bezier\(|\)$/g, "")
    .split(",")
    .map(Number);
  return [a, b, c, d];
}

export function toTransition(preset: MotionPreset, index = 0) {
  return {
    duration: preset.duration / 1000,
    ease: parseBezier(preset.easing),
    delay: (preset.stagger * index) / 1000,
  };
}

// Opacity/transform-only enter and exit states for motion/react components.
export function getMotionVariants(reducedMotion = false) {
  const { enter, exit } = getMotionPresets(reducedMotion);
  return {
    hidden: { opacity: 0, y: enter.distance },
    visible: (index: number = 0) => ({
      opacity: 1,
      y: 0,
      transition: toTransition(enter, index),
    }),
    exit: { opacity: 0, y: -exit.distance, transition: toTransition(exit) },
  };
}
