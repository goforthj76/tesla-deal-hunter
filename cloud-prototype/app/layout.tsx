import type {Metadata} from 'next';
import './globals.css';
export const metadata:Metadata={title:'Tesla Deal Hunter',description:'Private Tesla inventory research and live scan tracking'};
export default function RootLayout({children}:{children:React.ReactNode}){return <html lang="en"><body>{children}</body></html>}
