import React from 'react';
import { Typography, Tooltip } from 'antd';
import { ExclamationCircleOutlined } from '@ant-design/icons';

const { Text } = Typography;

export interface ServiceTopology {
  [serviceName: string]: {
    upstream: string[];
    downstream: string[];
  };
}

interface ServiceFlowDiagramProps {
  topology: ServiceTopology;
  errorServices?: string[];
}

/* --- PLACEHOLDER_SERVICE_FLOW_REST --- */

const nodeColors = [
  '#1677ff', '#722ed1', '#13c2c2', '#52c41a', '#fa8c16',
  '#eb2f96', '#2f54eb', '#a0d911', '#f5222d', '#fadb14',
];

function buildLayers(topology: ServiceTopology): string[][] {
  const services = Object.keys(topology);
  if (services.length === 0) return [];

  const hasUpstream = new Set<string>();
  const hasDownstream = new Set<string>();

  for (const [svc, rel] of Object.entries(topology)) {
    if (rel.downstream.length > 0) hasDownstream.add(svc);
    for (const ds of rel.downstream) hasUpstream.add(ds);
  }

  // Root nodes: have downstream but no upstream
  const roots = services.filter(s => !hasUpstream.has(s) && hasDownstream.has(s));
  // Leaf nodes: have upstream but no downstream
  const leaves = services.filter(s => hasUpstream.has(s) && !hasDownstream.has(s));
  // Middle nodes: have both
  const middles = services.filter(s => hasUpstream.has(s) && hasDownstream.has(s));
  // Isolated: neither
  const isolated = services.filter(s => !hasUpstream.has(s) && !hasDownstream.has(s));

  const layers: string[][] = [];
  if (roots.length > 0) layers.push(roots);
  if (middles.length > 0) layers.push(middles);
  if (leaves.length > 0) layers.push(leaves);
  if (isolated.length > 0) layers.push(isolated);

  // Deduplicate across layers
  const seen = new Set<string>();
  return layers.map(layer => {
    const unique = layer.filter(s => !seen.has(s));
    unique.forEach(s => seen.add(s));
    return unique;
  }).filter(l => l.length > 0);
}

const Arrow: React.FC = () => (
  <svg width="28" height="20" viewBox="0 0 28 20" style={{ flexShrink: 0 }}>
    <line x1="0" y1="10" x2="20" y2="10" stroke="var(--lm-text-tertiary)" strokeWidth="1.5" strokeDasharray="3,2" />
    <polygon points="20,6 28,10 20,14" fill="var(--lm-text-tertiary)" />
  </svg>
);

const ServiceFlowDiagram: React.FC<ServiceFlowDiagramProps> = ({ topology, errorServices = [] }) => {
  const layers = buildLayers(topology);
  const allServices = Object.keys(topology);
  const errorSet = new Set(errorServices);

  if (allServices.length === 0) return null;

  return (
    <div style={{
      padding: '12px 16px',
      borderRadius: 14,
      background: 'var(--lm-bg-card)',
      border: '1px solid var(--lm-border-light)',
      marginBottom: 12,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <Text style={{ fontSize: 12, fontWeight: 600, color: 'var(--lm-text)' }}>
          服务调用拓扑
        </Text>
        <Text style={{ fontSize: 11, color: 'var(--lm-text-tertiary)' }}>
          {allServices.length} 个服务
        </Text>
      </div>

      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        overflowX: 'auto',
        padding: '8px 0',
      }}>
        {layers.map((layer, layerIdx) => (
          <React.Fragment key={layerIdx}>
            {layerIdx > 0 && <Arrow />}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {layer.map((service) => {
                const colorIdx = allServices.indexOf(service);
                const color = nodeColors[colorIdx % nodeColors.length];
                const isError = errorSet.has(service);

                return (
                  <Tooltip key={service} title={
                    <div>
                      <div>{service}</div>
                      {topology[service]?.upstream.length > 0 && (
                        <div style={{ fontSize: 11 }}>上游: {topology[service].upstream.join(', ')}</div>
                      )}
                      {topology[service]?.downstream.length > 0 && (
                        <div style={{ fontSize: 11 }}>下游: {topology[service].downstream.join(', ')}</div>
                      )}
                    </div>
                  }>
                    <div style={{
                      padding: '6px 12px',
                      borderRadius: 8,
                      border: `1.5px solid ${isError ? '#ff4d4f' : `${color}55`}`,
                      background: isError ? 'rgba(255,77,79,0.08)' : `${color}10`,
                      fontSize: 11,
                      fontWeight: 600,
                      color: isError ? '#ff4d4f' : color,
                      whiteSpace: 'nowrap',
                      display: 'flex',
                      alignItems: 'center',
                      gap: 4,
                    }}>
                      {isError && <ExclamationCircleOutlined style={{ fontSize: 11 }} />}
                      {service}
                    </div>
                  </Tooltip>
                );
              })}
            </div>
          </React.Fragment>
        ))}
      </div>
    </div>
  );
};

export default ServiceFlowDiagram;
