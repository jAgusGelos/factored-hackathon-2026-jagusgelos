// The vertical frame (1080x1920): social apps cover the top ~150 px and the bottom ~250 px with
// their own UI, so readable text stays between y 150 and y 1670.
export const TEASER_SIZE = { width: 1080, height: 1920 } as const;
export const MARGIN = 80;
export const TEXT_WIDTH = TEASER_SIZE.width - MARGIN * 2;

/** The builder's cut-out, standing in the lower part of the frame (his head sits around y 1350). */
export const PRESENTER_LOWER = { x: 90, y: 1130, w: 900, h: 790 } as const;
export const PRESENTER_BUBBLE_LOWER = { x: 770, y: 1300, w: 250, h: 250 } as const;
