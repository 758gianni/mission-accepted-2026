import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './assets/css/index.css';
import { Root } from './root';

const root = document.getElementById('root');

createRoot(root!).render(
	<StrictMode>
		<Root />
	</StrictMode>,
);
