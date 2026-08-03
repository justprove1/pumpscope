import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Memecoin Intelligence Terminal',
  description: 'Radar de tokens nuevos de Pump.fun. Solo lectura.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es">
      <body>{children}</body>
    </html>
  );
}
