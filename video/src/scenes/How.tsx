import React from 'react';
import { AbsoluteFill, interpolate, useCurrentFrame } from 'remotion';
import { PenPath, boxPath } from '../components/Pen';
import { PresenterTrack } from '../components/Presenter';
import { Appear, SfxTrack, VoiceTrack, WhileLine } from '../components/Scene';
import { Glow } from '../components/Stage';
import { HonestyChip, Karaoke, MonoLabel } from '../components/Type';
import { FACTS } from '../facts';
import { lineOf, sceneOf, wordFrame } from '../timeline';
import { COLOR, FONT, HEIGHT, WIDTH } from '../theme';

const scene = sceneOf('how');
const how1 = lineOf(scene, 'how1');
const how2 = lineOf(scene, 'how2');
const how3 = lineOf(scene, 'how3');

const NODE_Y = 420;
const NODE_H = 150;
const PEN_FRAMES = 14;

interface Node {
  x: number;
  w: number;
  title: string;
  note: string;
  at: number;
  color: string;
}

const NODES: Node[] = [
  { x: 660, w: 230, title: 'Customer', note: 'writes or taps', at: how1.from, color: COLOR.boneDim },
  { x: 950, w: 250, title: 'Model', note: 'reads: extracts, summarizes', at: wordFrame(how1, 'model'), color: COLOR.signal },
  { x: 1260, w: 250, title: 'Code', note: 'decides: policy, permissions', at: wordFrame(how1, 'rules'), color: COLOR.success },
  { x: 1570, w: 250, title: 'Ledger', note: 'verifies: their own charges', at: wordFrame(how1, 'ledger'), color: COLOR.warning },
];
const CREDIT = { x: 1260, y: 720, w: 250, h: 110 };
const strikeAt = wordFrame(how1, 'reads');
const decideAt = wordFrame(how1, 'code');

const DiagramNode: React.FC<{ node: Node }> = ({ node }) => (
  <Appear at={node.at + PEN_FRAMES - 4} rise={6} style={{ left: node.x + 18, top: NODE_Y + 22, width: node.w - 36 }}>
    <div style={{ fontFamily: FONT.display, fontWeight: 800, fontSize: 40, color: COLOR.bone }}>{node.title}</div>
    <MonoLabel size={16} color={node.color} style={{ marginTop: 10, letterSpacing: '0.1em' }}>
      {node.note}
    </MonoLabel>
  </Appear>
);

const Diagram: React.FC = () => (
  <AbsoluteFill>
    <svg width={WIDTH} height={HEIGHT} style={{ position: 'absolute' }}>
      {NODES.map((node) => (
        <PenPath key={node.title} d={boxPath(node.x, NODE_Y, node.w, NODE_H)} from={node.at} to={node.at + PEN_FRAMES} color={node.color} width={2} />
      ))}
      {NODES.slice(1).map((node, i) => {
        const prev = NODES[i];
        const y = NODE_Y + NODE_H / 2;
        return <PenPath key={`arrow-${node.title}`} d={`M ${prev.x + prev.w} ${y} L ${node.x} ${y}`} from={node.at + 4} to={node.at + 12} spark={false} />;
      })}
      <PenPath
        d={`M ${NODES[1].x + NODES[1].w / 2} ${NODE_Y + NODE_H} C ${NODES[1].x + 120} ${CREDIT.y + 40}, ${CREDIT.x - 120} ${CREDIT.y + 60}, ${CREDIT.x} ${CREDIT.y + CREDIT.h / 2}`}
        from={strikeAt}
        to={strikeAt + 16}
        dashed
        color={COLOR.critical}
      />
      <PenPath d={boxPath(CREDIT.x, CREDIT.y, CREDIT.w, CREDIT.h)} from={strikeAt} to={strikeAt + PEN_FRAMES} color={COLOR.boneDim} />
      <PenPath
        d={`M ${NODES[2].x + NODES[2].w / 2} ${NODE_Y + NODE_H} L ${CREDIT.x + CREDIT.w / 2} ${CREDIT.y}`}
        from={decideAt + 8}
        to={decideAt + 18}
        color={COLOR.success}
        width={3}
      />
    </svg>
    {NODES.map((node) => (
      <DiagramNode key={node.title} node={node} />
    ))}
    <Appear at={strikeAt + 6} style={{ left: CREDIT.x + 24, top: CREDIT.y + 30 }}>
      <div style={{ fontFamily: FONT.display, fontWeight: 800, fontSize: 36, color: COLOR.bone }}>Credit</div>
    </Appear>
    <Appear at={strikeAt + 14} style={{ left: NODES[1].x + 20, top: CREDIT.y - 10 }}>
      <div style={{ fontFamily: FONT.display, fontWeight: 900, fontSize: 70, color: COLOR.critical, lineHeight: 1 }}>×</div>
      <MonoLabel size={14} color={COLOR.critical}>
        no path from the model
      </MonoLabel>
    </Appear>
  </AbsoluteFill>
);

const BAND = { x: 660, y: 440, w: 1160, h: 120 };
const SLICE_MIN_W = 36;

const DataSlice: React.FC = () => {
  const frame = useCurrentFrame();
  const monthAt = wordFrame(how2, 'month');
  const sliceW = Math.max(SLICE_MIN_W, (BAND.w * FACTS.subset.rowCount) / FACTS.subset.totalRows);
  const shrink = interpolate(frame, [monthAt - 6, monthAt + 14], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  const fullOpacity = interpolate(shrink, [0, 1], [0.55, 0.18]);
  return (
    <AbsoluteFill>
      <Appear at={how2.from} style={{ left: BAND.x, top: BAND.y - 60 }}>
        <MonoLabel>{`${FACTS.subset.of} transaction rows in the dataset`}</MonoLabel>
      </Appear>
      <svg width={WIDTH} height={HEIGHT} style={{ position: 'absolute' }}>
        <defs>
          <pattern id="rows" width={6} height={BAND.h} patternUnits="userSpaceOnUse">
            <rect width={3} height={BAND.h} fill={COLOR.grid} />
          </pattern>
        </defs>
        <PenPath d={boxPath(BAND.x, BAND.y, BAND.w, BAND.h)} from={how2.from - 4} to={how2.from + 16} color={COLOR.boneDim} />
        <rect x={BAND.x} y={BAND.y} width={BAND.w} height={BAND.h} fill="url(#rows)" opacity={fullOpacity * Math.min(1, (frame - how2.from) / 12)} />
        {shrink > 0 ? (
          <rect
            x={BAND.x + BAND.w - sliceW}
            y={BAND.y - 8}
            width={sliceW}
            height={BAND.h + 16}
            fill={COLOR.signal}
            opacity={shrink}
            style={{ filter: `drop-shadow(0 0 16px ${COLOR.signal})` }}
          />
        ) : null}
      </svg>
      <Appear at={monthAt + 10} style={{ left: BAND.x + BAND.w - 700, top: BAND.y + BAND.h + 34, width: 700, textAlign: 'right' }}>
        <div style={{ fontFamily: FONT.display, fontWeight: 900, fontSize: 64, color: COLOR.signal }}>{FACTS.subset.rows}</div>
        <MonoLabel style={{ margin: '6px 0 14px' }}>{`rows · ${FACTS.subset.window}`}</MonoLabel>
        <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
          <HonestyChip kind={FACTS.subset.label} size={14}>
            extraction manifest
          </HonestyChip>
        </div>
      </Appear>
      <Appear at={wordFrame(how2, 'recent')} style={{ left: BAND.x, top: 800, width: 1160 }}>
        <HonestyChip kind="DESIGN ARGUMENT" size={16}>
          A dispute needs only the customer’s recent ledger
        </HonestyChip>
      </Appear>
      <Appear at={wordFrame(how2, 'live')} style={{ left: BAND.x, top: 860, width: 1160 }}>
        <MonoLabel size={16} color={COLOR.bone}>
          The live app reads one small fixture · no cloud credentials at runtime (AD-2)
        </MonoLabel>
      </Appear>
    </AbsoluteFill>
  );
};

const EvalResult: React.FC = () => {
  const zeroAt = wordFrame(how3, 'zero');
  return (
    <AbsoluteFill>
      <Appear at={zeroAt - 2} rise={24} style={{ left: 660, top: 330 }}>
        <Glow radius={24} strength={0.7}>
          <div style={{ fontFamily: FONT.display, fontWeight: 900, fontStretch: '118%', fontSize: 230, lineHeight: 1, color: COLOR.success }}>
            {FACTS.eval.unsafe}
            <span style={{ color: COLOR.boneDim, fontSize: 130 }}>{` / ${FACTS.eval.cases}`}</span>
          </div>
        </Glow>
        <MonoLabel size={24} color={COLOR.bone} style={{ marginTop: 8 }}>
          unsafe outcomes · prompt injection included
        </MonoLabel>
      </Appear>
      <Appear at={zeroAt + 10} style={{ left: 660, top: 720, width: 1160 }}>
        <HonestyChip kind={FACTS.eval.label} size={16}>
          {FACTS.eval.source}
        </HonestyChip>
        {FACTS.eval.pending ? (
          <div style={{ marginTop: 14 }}>
            <HonestyChip kind="PLACEHOLDER" size={14}>
              re-measured before release
            </HonestyChip>
          </div>
        ) : null}
      </Appear>
    </AbsoluteFill>
  );
};

export const How: React.FC = () => (
  <AbsoluteFill>
    <WhileLine line={how1} until={how2.from - 4}>
      <div style={{ position: 'absolute', left: 660, top: 110 }}>
        <Karaoke line={how1} size={44} width={1180} emphasis={['reads', 'code', 'ledger']} />
      </div>
      <Diagram />
    </WhileLine>
    <WhileLine line={how2} until={how3.from - 4}>
      <div style={{ position: 'absolute', left: 660, top: 110 }}>
        <Karaoke line={how2} size={44} width={1180} emphasis={['month']} />
      </div>
      <DataSlice />
    </WhileLine>
    <WhileLine line={how3} until={scene.frames}>
      <div style={{ position: 'absolute', left: 660, top: 110 }}>
        <Karaoke line={how3} size={44} width={1180} />
      </div>
      <EvalResult />
    </WhileLine>
    <PresenterTrack lines={scene.lines} mode="full" box={{ x: 60, y: 180, w: 540, h: 900 }} until={scene.frames} />
    <VoiceTrack scene={scene} />
    <SfxTrack
      cues={[
        ...NODES.slice(1).map((node) => ({ at: node.at, sfx: 'impactSoft_medium_001', volume: 0.18 })),
        { at: strikeAt + 14, sfx: 'switch_007', volume: 0.25 },
        { at: wordFrame(how3, 'zero'), sfx: 'impactSoft_heavy_003', volume: 0.35 },
      ]}
    />
  </AbsoluteFill>
);
