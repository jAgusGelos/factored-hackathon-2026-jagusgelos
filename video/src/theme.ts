// The reel's roles (ink, bone, signal) mapped onto the app's own tokens
// (.workspace/design/tokens.css), so the motion graphics read as the same brand as the UI
// inside the mockups. Status colours are the app's, lifted to read on the dark ink.
export const COLOR = {
  ink: '#0A1628',
  ink2: '#0E2545',
  panel: '#14335C',
  bone: '#F6F7F9',
  boneDim: 'rgba(246, 247, 249, 0.55)',
  grid: '#E8EEF6',
  signal: '#4C8DFF',
  signalHot: '#9CC2FF',
  success: '#3FBF7F',
  warning: '#F0A23A',
  critical: '#F06A60',
} as const;

export const FONT = {
  display: 'Archivo',
  mono: 'IBM Plex Mono',
  serif: 'Cormorant Garamond',
} as const;

export const FPS = 30;
export const WIDTH = 1920;
export const HEIGHT = 1080;

export const LINKS = {
  demo: 'factored-hackaton-latest.onrender.com',
  repo: 'github.com/jAgusGelos/factored-hackathon-2026-jagusgelos',
} as const;
