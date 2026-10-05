import React from 'react';
import { AbsoluteFill, Easing, interpolate, useCurrentFrame, useVideoConfig } from 'remotion';
import { Glow } from '../components/Stage';
import { PenPath, Spark, boxPath } from '../components/Pen';
import { PRESENTER_RIGHT, PresenterTrack } from '../components/Presenter';
import { Appear, SfxTrack, VoiceTrack } from '../components/Scene';
import { Caption, FactChip, LineKaraoke, MonoLabel } from '../components/Type';
import { FACTS } from '../facts';
import { lineOf, sceneOf, wordFrame } from '../timeline';
import { CLAMP, COLOR, FONT, HEIGHT, WIDTH, alpha } from '../theme';

const scene = sceneOf('cold');
const cold1 = lineOf(scene, 'cold1');
const cold2 = lineOf(scene, 'cold2');
const cold3 = lineOf(scene, 'cold3');

const PHONE = { x: 170, y: 470, w: 330, h: 560 };
const CLOCK: ClockLayout = { x: 760, y: 640, r: 230 };
const CLOCK_HOURS = 48;
const TEXT_AT = { x: 120, y: 120 };
const notificationAt = cold1.from + 4;
const stampAt = wordFrame(cold1, 'recognize');
const reportAt = wordFrame(cold2, 'report');
const waitAt = wordFrame(cold2, 'wait');
const countFrom = wordFrame(cold3, 'median');
const countTo = wordFrame(cold3, 'hours');

export const Cold: React.FC = () => {
  const frame = useCurrentFrame();
  const phoneOut = interpolate(frame, [cold3.from - 12, cold3.from + 6], [1, 0], CLAMP);
  return (
    <AbsoluteFill>
      <LineKaraoke line={cold1} until={cold2.from - 4} at={TEXT_AT} size={62} width={1100} emphasis={['recognize']} />
      <LineKaraoke line={cold2} until={cold3.from - 4} at={TEXT_AT} size={62} width={1100} emphasis={['wait']} />
      <LineKaraoke line={cold3} until={scene.frames} at={TEXT_AT} size={62} width={1040} emphasis={['thirty-seven', 'hours']} />

      <AbsoluteFill style={{ opacity: phoneOut }}>
        <svg width={WIDTH} height={HEIGHT} style={{ position: 'absolute' }}>
          <PenPath d={boxPath(PHONE.x, PHONE.y, PHONE.w, PHONE.h)} from={4} to={cold1.from + 10} width={2} />
          <PenPath d={`M ${PHONE.x + 120} ${PHONE.y + 22} L ${PHONE.x + 210} ${PHONE.y + 22}`} from={cold1.from} to={cold1.from + 8} spark={false} />
          <PenPath
            d={`M ${PHONE.x + PHONE.w + 40} ${PHONE.y + 330} L ${PHONE.x + PHONE.w + 520} ${PHONE.y + 330}`}
            from={stampAt}
            to={stampAt + 14}
            color={COLOR.critical}
            width={3}
          />
          <PenPath
            d={`M ${PHONE.x + PHONE.w + 40} ${PHONE.y + 540} L ${PHONE.x + PHONE.w + 640} ${PHONE.y + 540}`}
            from={reportAt + 6}
            to={cold3.from}
            dashed
            color={COLOR.boneDim}
          />
        </svg>
        <Appear at={notificationAt} style={{ left: PHONE.x + 18, top: PHONE.y + 90, width: PHONE.w - 36 }}>
          <div style={{ background: alpha(COLOR.bone, 0.96), borderRadius: 16, padding: '16px 18px', color: '#1a2130', fontFamily: FONT.display }}>
            <div style={{ fontSize: 15, color: '#5b6472', fontWeight: 600, letterSpacing: '0.06em' }}>LATAM BANK · AHORA</div>
            <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4 }}>Nueva compra</div>
            <div style={{ fontSize: 20 }}>Tienda Online Global</div>
            <div style={{ fontSize: 24, fontWeight: 800, marginTop: 4 }}>COP 689.000</div>
          </div>
        </Appear>
        <Appear at={notificationAt + 8} style={{ left: PHONE.x + 18, top: PHONE.y + 250, width: PHONE.w - 36 }}>
          <MonoLabel size={15}>New purchase</MonoLabel>
          <MonoLabel size={13} style={{ marginTop: 6 }}>illustrative</MonoLabel>
        </Appear>
        <Appear at={stampAt} style={{ left: PHONE.x + PHONE.w + 40, top: PHONE.y + 250 }}>
          <Glow radius={10} strength={0.6}>
            <div style={{ fontFamily: FONT.display, fontWeight: 900, fontStretch: '125%', fontSize: 64, color: COLOR.critical, letterSpacing: '0.02em' }}>
              UNRECOGNIZED
            </div>
          </Glow>
        </Appear>
        <Appear at={reportAt} style={{ left: PHONE.x + 18, top: PHONE.y + 400, width: PHONE.w - 36 }}>
          <Caption original="Reporte enviado" english="Report sent." style={{ padding: '10px 14px' }} />
        </Appear>
        <Appear at={waitAt} style={{ left: PHONE.x + PHONE.w + 40, top: PHONE.y + 490 }}>
          <MonoLabel>waiting for a first response…</MonoLabel>
        </Appear>
      </AbsoluteFill>

      <WaitClock clock={CLOCK} appearAt={cold3.from - 10} countFrom={countFrom} countTo={countTo}>
        <Appear at={countTo + 4} style={{ left: CLOCK.x - CLOCK.r - 200, top: CLOCK.y + CLOCK.r + 40, width: 860 }}>
          <FactChip fact={FACTS.firstResponse} long />
        </Appear>
      </WaitClock>

      <PresenterTrack lines={scene.lines} mode="full" box={PRESENTER_RIGHT} until={scene.frames} />
      <VoiceTrack scene={scene} />
      <SfxTrack
        cues={[
          { at: notificationAt, sfx: 'bong_001', volume: 0.35 },
          { at: stampAt, sfx: 'impactSoft_medium_001', volume: 0.35 },
          { at: countTo, sfx: 'impactSoft_heavy_003', volume: 0.4 },
        ]}
      />
    </AbsoluteFill>
  );
};

export interface ClockLayout {
  x: number;
  y: number;
  r: number;
}

/** The radius the clock's type sizes were drawn for; other radii scale them. */
const DESIGN_R = 230;

const ClockTick: React.FC<{ clock: ClockLayout; hour: number }> = ({ clock, hour }) => {
  const a = -Math.PI / 2 + (hour / CLOCK_HOURS) * Math.PI * 2;
  const major = hour % 6 === 0;
  const inner = major ? clock.r - 26 : clock.r - 12;
  return (
    <line
      x1={clock.x + inner * Math.cos(a)}
      y1={clock.y + inner * Math.sin(a)}
      x2={clock.x + clock.r * Math.cos(a)}
      y2={clock.y + clock.r * Math.sin(a)}
      stroke={COLOR.boneDim}
      strokeWidth={major ? 2 : 1}
    />
  );
};

/** The 37 h wait drawn as a 48 h dial: the arc and the number count up together and land on `countTo`. */
export const WaitClock: React.FC<{ clock: ClockLayout; appearAt: number; countFrom: number; countTo: number; children?: React.ReactNode }> = ({
  clock,
  appearAt,
  countFrom,
  countTo,
  children,
}) => {
  const frame = useCurrentFrame();
  const { width, height } = useVideoConfig();
  if (frame < appearAt) return null;
  const p = interpolate(frame, [countFrom, countTo + 6], [0, 1], {
    ...CLAMP,
    easing: Easing.inOut(Easing.cubic),
  });
  const appear = interpolate(frame, [appearAt, appearAt + 16], [0, 1], { extrapolateRight: 'clamp' });
  const k = clock.r / DESIGN_R;
  const hours = Math.round(p * FACTS.firstResponse.hours);
  const angle = -Math.PI / 2 + p * Math.PI * 2 * (FACTS.firstResponse.hours / CLOCK_HOURS);
  const circumference = 2 * Math.PI * clock.r;
  const arc = circumference * p * (FACTS.firstResponse.hours / CLOCK_HOURS);
  const head = { x: clock.x + clock.r * Math.cos(angle), y: clock.y + clock.r * Math.sin(angle) };
  const landed = frame >= countTo;
  return (
    <AbsoluteFill style={{ opacity: appear }}>
      <svg width={width} height={height} style={{ position: 'absolute' }}>
        <circle cx={clock.x} cy={clock.y} r={clock.r} fill="none" stroke={COLOR.boneDim} strokeOpacity={0.35} strokeWidth={2} />
        {Array.from({ length: CLOCK_HOURS }, (_, hour) => (
          <ClockTick key={hour} clock={clock} hour={hour} />
        ))}
        <circle
          cx={clock.x}
          cy={clock.y}
          r={clock.r}
          fill="none"
          stroke={COLOR.signal}
          strokeWidth={6}
          strokeDasharray={`${arc} ${circumference}`}
          transform={`rotate(-90 ${clock.x} ${clock.y})`}
          style={{ filter: `drop-shadow(0 0 10px ${COLOR.signal})` }}
        />
        {p > 0 && !landed ? <Spark x={head.x} y={head.y} /> : null}
      </svg>
      <div style={{ position: 'absolute', left: clock.x - 260 * k, top: clock.y - 110 * k, width: 520 * k, textAlign: 'center' }}>
        <Glow radius={landed ? 22 : 8} strength={landed ? 0.9 : 0.4}>
          <div style={{ fontFamily: FONT.display, fontWeight: 900, fontStretch: '115%', fontSize: 180 * k, lineHeight: 1, color: landed ? COLOR.signal : COLOR.bone }}>
            {hours}
            <span style={{ fontSize: 90 * k }}> h</span>
          </div>
        </Glow>
        <MonoLabel size={18 * k} style={{ marginTop: 10 }}>
          median wait for a first response
        </MonoLabel>
      </div>
      {children}
    </AbsoluteFill>
  );
};
