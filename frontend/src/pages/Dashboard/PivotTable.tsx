import React, { useState, useEffect, useMemo } from 'react';
import { Typography, Space, Tag, Select, Spin, Empty, Card, Tooltip } from 'antd';
import { TableOutlined } from '@ant-design/icons';
import client from '@/api/client';

const { Title, Text } = Typography;

const dimLabels: Record<string, string> = {
  service: '服务', severity: '严重度', type: '类型', date: '日期',
};

const PivotTable: React.FC = () => {
  const [rowDim, setRowDim] = useState('service');
  const [colDim, setColDim] = useState('severity');
  const [metric, setMetric] = useState('count');
  const [days, setDays] = useState(7);
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      try {
        const res = await client.get('/dashboard/pivot', { params: { row_dim: rowDim, col_dim: colDim, metric, days } });
        setData(res.data);
      } catch { /* ignore */ }
      setLoading(false);
    };
    load();
  }, [rowDim, colDim, metric, days]);

  // Build matrix
  const { matrix, rowTotals, colTotals, maxVal } = useMemo(() => {
    if (!data) return { matrix: {}, rowTotals: {}, colTotals: {}, maxVal: 1 };
    const m: Record<string, Record<string, number>> = {};
    const rTotals: Record<string, number> = {};
    const cTotals: Record<string, number> = {};
    let max = 0;

    for (const cell of (data.cells || [])) {
      if (!m[cell.row]) m[cell.row] = {};
      m[cell.row][cell.col] = cell.value;
      rTotals[cell.row] = (rTotals[cell.row] || 0) + cell.value;
      cTotals[cell.col] = (cTotals[cell.col] || 0) + cell.value;
      if (cell.value > max) max = cell.value;
    }
    return { matrix: m, rowTotals: rTotals, colTotals: cTotals, maxVal: max || 1 };
  }, [data]);

  const heatColor = (v: number) => {
    if (v === 0) return 'transparent';
    const ratio = Math.min(v / maxVal, 1);
    if (ratio < 0.25) return 'rgba(22,119,255,0.15)';
    if (ratio < 0.5) return 'rgba(22,119,255,0.35)';
    if (ratio < 0.75) return 'rgba(114,46,209,0.45)';
    return 'rgba(255,77,79,0.55)';
  };

  return (
    <div className="lm-animate-in">
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Space>
          <TableOutlined style={{ fontSize: 20, color: '#722ed1' }} />
          <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>多维分析透视表</Title>
        </Space>
        <Space>
          <Select value={rowDim} onChange={setRowDim} size="small" style={{ width: 100 }}
            options={[{ value: 'service', label: '行: 服务' }, { value: 'severity', label: '行: 严重度' }, { value: 'type', label: '行: 类型' }, { value: 'date', label: '行: 日期' }]} />
          <Select value={colDim} onChange={setColDim} size="small" style={{ width: 100 }}
            options={[{ value: 'severity', label: '列: 严重度' }, { value: 'service', label: '列: 服务' }, { value: 'type', label: '列: 类型' }, { value: 'date', label: '列: 日期' }]} />
          <Select value={days} onChange={setDays} size="small" style={{ width: 100 }}
            options={[{ value: 1, label: '1 天' }, { value: 7, label: '7 天' }, { value: 14, label: '14 天' }, { value: 30, label: '30 天' }]} />
        </Space>
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 80 }}><Spin size="large" /></div>
      ) : !data?.rows?.length ? (
        <Empty description="暂无数据" />
      ) : (
        <Card style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12, overflow: 'hidden' }}
          styles={{ body: { padding: 0 } }}>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr>
                  <th style={{
                    padding: '10px 16px', textAlign: 'left',
                    borderBottom: '1px solid var(--lm-border-light)',
                    color: 'var(--lm-text-secondary)', fontWeight: 600, fontSize: 12,
                    background: 'var(--lm-bg-elevated)',
                  }}>
                    {dimLabels[rowDim]} \ {dimLabels[colDim]}
                  </th>
                  {(data.cols || []).map((col: string) => (
                    <th key={col} style={{
                      padding: '10px 12px', textAlign: 'center',
                      borderBottom: '1px solid var(--lm-border-light)',
                      color: 'var(--lm-text-secondary)', fontWeight: 600, fontSize: 12,
                      background: 'var(--lm-bg-elevated)',
                    }}>{col}</th>
                  ))}
                  <th style={{
                    padding: '10px 12px', textAlign: 'center',
                    borderBottom: '1px solid var(--lm-border-light)',
                    color: '#1677ff', fontWeight: 700, fontSize: 12,
                    background: 'var(--lm-bg-elevated)',
                  }}>合计</th>
                </tr>
              </thead>
              <tbody>
                {(data.rows || []).map((row: string, ri: number) => (
                  <tr key={row} style={{ animation: `lm-fadeSlideIn 0.3s ease-out ${ri * 0.03}s both` }}>
                    <td style={{
                      padding: '8px 16px',
                      borderBottom: '1px solid rgba(255,255,255,0.03)',
                      color: 'var(--lm-text)', fontWeight: 500,
                    }}>{row}</td>
                    {(data.cols || []).map((col: string) => {
                      const val = matrix[row]?.[col] || 0;
                      return (
                        <td key={col} style={{
                          padding: '8px 12px', textAlign: 'center',
                          borderBottom: '1px solid rgba(255,255,255,0.03)',
                          background: heatColor(val),
                          fontFamily: 'monospace', fontWeight: val > 0 ? 600 : 400,
                          color: val > 0 ? 'var(--lm-text)' : 'var(--lm-text-tertiary)',
                          transition: 'background 0.3s',
                        }}>
                          {val > 0 ? val : '—'}
                        </td>
                      );
                    })}
                    <td style={{
                      padding: '8px 12px', textAlign: 'center',
                      borderBottom: '1px solid rgba(255,255,255,0.03)',
                      fontFamily: 'monospace', fontWeight: 700, color: '#1677ff',
                    }}>
                      {rowTotals[row] || 0}
                    </td>
                  </tr>
                ))}
                {/* Totals row */}
                <tr style={{ background: 'var(--lm-bg-elevated)' }}>
                  <td style={{ padding: '8px 16px', fontWeight: 700, color: '#1677ff' }}>合计</td>
                  {(data.cols || []).map((col: string) => (
                    <td key={col} style={{ padding: '8px 12px', textAlign: 'center', fontFamily: 'monospace', fontWeight: 700, color: '#1677ff' }}>
                      {colTotals[col] || 0}
                    </td>
                  ))}
                  <td style={{ padding: '8px 12px', textAlign: 'center', fontFamily: 'monospace', fontWeight: 800, color: '#fadb14' }}>
                    {Object.values(rowTotals).reduce((a: number, b: number) => a + b, 0)}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
};

export default PivotTable;
