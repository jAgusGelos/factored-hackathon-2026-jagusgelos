import React from 'react';
import { AbsoluteFill, Easing, interpolate, useCurrentFrame } from 'remotion';
import { Glow } from '../components/Stage';
import { PenPath, Spark, boxPath } from '../components/Pen';
import { PresenterTrack } from '../components/Presenter';
import { Appear, SfxTrack, VoiceTrack, WhileLine } from '../components/Scene';
import { Caption, HonestyChip, Karaoke, MonoLabel } from '../components/Type';
import { FACTS } from '../facts';
import { lineOf, sceneOf, wordFrame } from '../timeline';
import { COLOR, FONT, HEIGHT, WIDTH } from '../theme';

const scene = sceneOf('cold');
const cold1 = lineOf(scene, 'cold1');
const cold2 = lineOf(scene, 'cold2');
const cold3 = lineOf(scene, 'cold3');

const PHONE = { x: 170, y: 470, w: 330, h: 560 };
const CLOCK = { x: 760, y: 640, r: 230 };
const notificationAt = cold1.from + 4;
const stampAt = wordFrame(cold1, 'recognize');
const reportAt = wordFrame(cold2, 'report');
const waitAt = wordFrame(cold2, 'wait');
const countFrom = wordFrame(cold3, 'median');
const countTo = wordFrame(cold3, 'hours');

export const Cold: React.FC = () => {
  const frame = useCurrentFrame();
  const phoneOut = interpolate(frame, [cold3.from - 12, cold3.from + 6], [1, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  return (
    <AbsoluteFill>
      <WhileLine line={cold1} until={cold2.from - 4}>
        <div style={{ position: 'absolute', left: 120, top: 120 }}>
          <Karaoke line={cold1} size={62} width={1100} emphasis={['recognize']} />
        </div>
      </WhileLine>
      <WhileLine line={cold2} until={cold3.from - 4}>
        <div style={{ position: 'absolute', left: 120, top: 120 }}>
          <Karaoke line={cold2} size={62} width={1100} emphasis={['wait']} />
        </div>
      </WhileLine>
      <WhileLine line={cold3} until={scene.frames}>
        <div style={{ position: 'absolute', left: 120, top: 120 }}>
          <Karaoke line={cold3} size={62} width={1040} emphasis={['thirty-seven', 'hours']} />
        </div>
      </WhileLine>

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
          <div style={{ background: 'rgba(246,247,249,0.96)', borderRadius: 16, padding: '16px 18px', color: '#1a2130', fontFamily: FONT.display }}>
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

      <WaitClock />

      <PresenterTrack lines={scene.lines} mode="full" box={{ x: 1260, y: 150, w: 600, h: 930 }} until={scene.frames} />
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

/** The 37 h clock: a ring sweeps while the hour counter races to the measured median. */
const WaitClock: React.FC = () => {
  const frame = useCurrentFrame();
  if (frame < cold3.from - 10) return null;
  const p = interpolate(frame, [countFrom, countTo + 6], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.inOut(Easing.cubic),
  });
  const appear = interpolate(frame, [cold3.from - 10, cold3.from + 6], [0, 1], { extrapolateRight: 'clamp' });
  const hours = Math.round(p * FACTS.firstResponse.hours);
  const angle = -Math.PI / 2 + p * Math.PI * 2 * (FACTS.firstResponse.hours / 48);
  const circumference = 2 * Math.PI * CLOCK.r;
  const arc = circumference * p * (FACTS.firstResponse.hours / 48);
  const head = { x: CLOCK.x + CLOCK.r * Math.cos(angle), y: CLOCK.y + CLOCK.r * Math.sin(angle) };
  const landed = frame >= countTo;
  return (
    <AbsoluteFill style={{ opacity: appear }}>
      <svg width={WIDTH} height={HEIGHT} style={{ position: 'absolute' }}>
        <circle cx={CLOCK.x} cy={CLOCK.y} r={CLOCK.r} fill="none" stroke={COLOR.boneDim} strokeOpacity={0.35} strokeWidth={2} />
        {Array.from({ length: 48 }, (_, i) => {
          const a = -Math.PI / 2 + (i / 48) * Math.PI * 2;
          const inner = i % 6 === 0 ? CLOCK.r - 26 : CLOCK.r - 12;
          return (
            <line
              key={i}
              x1={CLOCK.x + inner * Math.cos(a)}
              y1={CLOCK.y + inner * Math.sin(a)}
              x2={CLOCK.x + CLOCK.r * Math.cos(a)}
              y2={CLOCK.y + CLOCK.r * Math.sin(a)}
              stroke={COLOR.boneDim}
              strokeWidth={i % 6 === 0 ? 2 : 1}
            />
          );
        })}
        <circle
          cx={CLOCK.x}
          cy={CLOCK.y}
          r={CLOCK.r}
          fill="none"
          stroke={COLOR.signal}
          strokeWidth={6}
          strokeDasharray={`${arc} ${circumference}`}
          transform={`rotate(-90 ${CLOCK.x} ${CLOCK.y})`}
          style={{ filter: `drop-shadow(0 0 10px ${COLOR.signal})` }}
        />
        {p > 0 && !landed ? <Spark x={head.x} y={head.y} /> : null}
      </svg>
      <div style={{ position: 'absolute', left: CLOCK.x - 260, top: CLOCK.y - 110, width: 520, textAlign: 'center' }}>
        <Glow radius={landed ? 22 : 8} strength={landed ? 0.9 : 0.4}>
          <div style={{ fontFamily: FONT.display, fontWeight: 900, fontStretch: '115%', fontSize: 180, lineHeight: 1, color: landed ? COLOR.signal : COLOR.bone }}>
            {hours}
            <span style={{ fontSize: 90 }}> h</span>
          </div>
        </Glow>
        <MonoLabel style={{ marginTop: 10 }}>median wait for a first response</MonoLabel>
      </div>
      <Appear at={countTo + 4} style={{ left: CLOCK.x - CLOCK.r - 200, top: CLOCK.y + CLOCK.r + 40, width: 860 }}>
        <HonestyChip kind={FACTS.firstResponse.label}>{FACTS.firstResponse.source}</HonestyChip>
      </Appear>
    </AbsoluteFill>
  );
};
