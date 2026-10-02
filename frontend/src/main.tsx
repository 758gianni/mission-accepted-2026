import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './app.tsx';
import './assets/css/index.css';
import { Header } from './components/header.tsx';
import { Sidebar } from './components/sidebar.tsx';

const root = document.getElementById('root');

createRoot(root!).render(
	<StrictMode>
		<div className='min-h-screen flex flex-col'>
			<Header />

			<div className='flex flex-1 flex-wrap'>
				<App />
				<Sidebar />
			</div>
		</div>
	</StrictMode>,
);
