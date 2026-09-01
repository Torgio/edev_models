import type { Metadata } from 'next';
import { Geist, Geist_Mono } from 'next/font/google';
import './globals.css';

const geistSans = Geist({ variable: '--font-geist-sans', subsets: ['latin'] });
const geistMono = Geist_Mono({ variable: '--font-geist-mono', subsets: ['latin'] });

export const metadata: Metadata = {
  metadataBase: new URL('https://pulso-energia-tfm.maguicervinio.chatgpt.site'),
  title: 'Pulso Energía · Previsión eléctrica',
  description: 'Dashboard operativo de predicción del precio eléctrico español y rendimiento BESS.',
  openGraph: {
    title: 'Pulso Energía · Previsión eléctrica',
    description: 'Previsión eléctrica, comparación de modelos y plan operativo BESS. Acceso del equipo.',
    type: 'website',
    locale: 'es_ES',
    images: [{ url: 'https://pulso-energia-tfm.maguicervinio.chatgpt.site/og.png', alt: 'Pulso Energía · Previsión eléctrica · Modelos · BESS' }],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Pulso Energía · Previsión eléctrica',
    description: 'Previsión eléctrica, comparación de modelos y plan operativo BESS. Acceso del equipo.',
    images: ['https://pulso-energia-tfm.maguicervinio.chatgpt.site/og.png'],
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="es"><body className={`${geistSans.variable} ${geistMono.variable}`}>{children}</body></html>;
}
