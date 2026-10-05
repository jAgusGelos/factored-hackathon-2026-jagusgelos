import { continueRender, delayRender, staticFile } from 'remotion';

const FACES: { family: string; file: string; descriptors: FontFaceDescriptors }[] = [
  { family: 'Archivo', file: 'fonts/Archivo-Variable.ttf', descriptors: { weight: '100 900', stretch: '62% 125%' } },
  { family: 'IBM Plex Mono', file: 'fonts/IBMPlexMono-Regular.ttf', descriptors: { weight: '400' } },
  { family: 'IBM Plex Mono', file: 'fonts/IBMPlexMono-Medium.ttf', descriptors: { weight: '500' } },
  { family: 'Cormorant Garamond', file: 'fonts/CormorantGaramond-Italic-Variable.ttf', descriptors: { weight: '300 700', style: 'italic' } },
];

let loading: Promise<void> | null = null;

export function loadFonts(): void {
  if (loading) return;
  const handle = delayRender('fonts');
  loading = Promise.all(
    FACES.map(async ({ family, file, descriptors }) => {
      const face = new FontFace(family, `url(${staticFile(file)})`, descriptors);
      document.fonts.add(await face.load());
    }),
  ).then(() => continueRender(handle));
}
