import { useEffect, useMemo, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import type { Contract, RegionCollection, TemporalClassId } from '../data/types';
import { asset } from '../data/load';

type LayerKey = 'temporalRgb' | 'seasonal' | 'persistent' | 'terrainQa';

interface Props {
	contract: Contract;
	regions: RegionCollection;
	activeClasses: Set<TemporalClassId>;
	layers: Record<LayerKey, boolean>;
	selectedId: number | null;
	onSelect: (id: number) => void;
	focusBounds?: [[number, number], [number, number]] | null;
}

const CLASS_STYLE: Record<string, { color: string; fillOpacity: number }> = {
	seasonal_transient: { color: '#22b8cf', fillOpacity: 0.18 },
	persistent: { color: '#e14c8c', fillOpacity: 0.32 },
	t4_validated_persistent: { color: '#b0306a', fillOpacity: 0.42 },
	late_unclassified: { color: '#9a9e9b', fillOpacity: 0.18 },
};

const MapView = ({ contract, regions, activeClasses, layers, selectedId, onSelect, focusBounds }: Props) => {
	const containerRef = useRef<HTMLDivElement | null>(null);
	const mapRef = useRef<L.Map | null>(null);
	const overlayRefs = useRef<Record<string, L.Layer>>({});
	const geoRef = useRef<L.GeoJSON | null>(null);
	const clickRef = useRef(onSelect);
	useEffect(() => {
		clickRef.current = onSelect;
	}, [onSelect]);

	const b = contract.image_bounds;
	const bounds = useMemo(() => L.latLngBounds([b.south, b.west], [b.north, b.east]), [b.south, b.west, b.north, b.east]);

	useEffect(() => {
		if (!containerRef.current || mapRef.current) return;
		const map = L.map(containerRef.current, {
			zoomControl: true,
			attributionControl: true,
			minZoom: 7,
			maxZoom: 15,
		});
		map.fitBounds(bounds, { padding: [8, 8] });
		L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
			attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
			maxZoom: 15,
		}).addTo(map);
		mapRef.current = map;
		return () => {
			map.remove();
			mapRef.current = null;
		};
	}, [bounds]);

	// image overlays
	useEffect(() => {
		const map = mapRef.current;
		if (!map) return;
		const overlays: Record<string, L.Layer> = {
			temporalRgb: L.imageOverlay(asset('data/imagery/temporal_rgb.png'), bounds, { opacity: 0.92, interactive: false }),
			terrainQa: L.imageOverlay(asset('data/imagery/terrain_qa_mask.png'), bounds, { opacity: 0.55, interactive: false }),
		};
		for (const [key, layer] of Object.entries(overlays)) {
			overlayRefs.current[key] = layer;
		}
		return () => {
			for (const layer of Object.values(overlays)) layer.remove();
		};
	}, [bounds]);

	useEffect(() => {
		const map = mapRef.current;
		if (!map) return;
		const rgb = overlayRefs.current.temporalRgb;
		if (!rgb) return;
		if (layers.temporalRgb) rgb.addTo(map);
		else rgb.remove();
	}, [layers.temporalRgb]);

	useEffect(() => {
		const map = mapRef.current;
		if (!map) return;
		const qa = overlayRefs.current.terrainQa;
		if (!qa) return;
		if (layers.terrainQa) qa.addTo(map);
		else qa.remove();
	}, [layers.terrainQa]);

	// region polygons
	useEffect(() => {
		const map = mapRef.current;
		if (!map) return;
		if (geoRef.current) {
			geoRef.current.remove();
			geoRef.current = null;
		}

		const visible: RegionCollection = {
			type: 'FeatureCollection',
			features: regions.features.filter((f) => activeClasses.has(f.properties.temporal_class)),
		};

		const geo = L.geoJSON(visible as unknown as GeoJSON.GeoJsonObject, {
			style: (feature) => {
				const props = feature?.properties as { temporal_class: string } | undefined;
				const style = CLASS_STYLE[props?.temporal_class ?? 'persistent'] ?? CLASS_STYLE.persistent;
				return {
					color: style.color,
					weight: 1.2,
					opacity: 0.85,
					fillColor: style.color,
					fillOpacity: style.fillOpacity,
				};
			},
			onEachFeature: (feature, layer) => {
				const props = feature.properties as { id: number; label: string };
				layer.on('click', () => clickRef.current(props.id));
			},
		}).addTo(map);

		// seasonal/persistent visibility is folded into activeClasses; the
		// dedicated layer toggles dim rather than remove so a presenter can
		// show both without losing context.
		if (!layers.seasonal) {
			geo.eachLayer((layer) => {
				const p = ((layer as L.Layer & { feature?: { properties?: { temporal_class?: string } } }).feature?.properties ?? {}) as {
					temporal_class?: string;
				};
				if (p.temporal_class === 'seasonal_transient') (layer as L.Path).setStyle({ fillOpacity: 0, opacity: 0.15 });
			});
		}
		if (!layers.persistent) {
			geo.eachLayer((layer) => {
				const p = ((layer as L.Layer & { feature?: { properties?: { temporal_class?: string } } }).feature?.properties ?? {}) as {
					temporal_class?: string;
				};
				if (p.temporal_class === 'persistent' || p.temporal_class === 't4_validated_persistent') {
					(layer as L.Path).setStyle({ fillOpacity: 0, opacity: 0.15 });
				}
			});
		}

		geoRef.current = geo;
		return () => {
			geo.remove();
		};
	}, [regions, activeClasses, layers.seasonal, layers.persistent]);

	// selection highlight
	useEffect(() => {
		const map = mapRef.current;
		const geo = geoRef.current;
		if (!map || !geo) return;
		geo.eachLayer((layer) => {
			const p = ((layer as L.Layer & { feature?: { properties?: { id?: number } } }).feature?.properties ?? {}) as {
				id?: number;
			};
			const path = layer as L.Path;
			if (selectedId != null && p.id === selectedId) {
				path.setStyle({ weight: 3, color: '#1c2620', fillOpacity: 0.5 });
				path.bringToFront();
			}
		});
	}, [selectedId, regions, activeClasses, layers.seasonal, layers.persistent]);

	// presentation focus
	useEffect(() => {
		const map = mapRef.current;
		if (!map || !focusBounds) return;
		map.fitBounds(focusBounds, { padding: [40, 40], animate: true });
	}, [focusBounds]);

	return (
		<div className='relative h-[420px] w-full overflow-hidden rounded-md border border-rule sm:h-[600px] lg:h-[calc(100vh-260px)] lg:min-h-[520px]'>
			<div ref={containerRef} className='absolute inset-0' aria-label='Candidate region map' />
			<div className='pointer-events-none absolute bottom-2 left-2 z-[500] flex flex-wrap gap-2 rounded border border-rule bg-panel/92 px-2.5 py-1.5 text-[11px] text-body'>
				<span className='flex items-center gap-1.5'>
					<span className='size-2.5 rounded-sm' style={{ background: '#e14c8c' }} /> persistent
				</span>
				<span className='flex items-center gap-1.5'>
					<span className='size-2.5 rounded-sm' style={{ background: '#b0306a' }} /> T4-validated
				</span>
				<span className='flex items-center gap-1.5'>
					<span className='size-2.5 rounded-sm' style={{ background: '#22b8cf' }} /> seasonal
				</span>
				<span className='flex items-center gap-1.5'>
					<span className='size-2.5 rounded-sm' style={{ background: '#9a9e9b' }} /> late
				</span>
			</div>
		</div>
	);
};

export { MapView };
export type { LayerKey };
