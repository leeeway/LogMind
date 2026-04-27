import React from 'react';
import { Result, Button, Typography } from 'antd';

const { Paragraph, Text } = Typography;

interface State {
  hasError: boolean;
  error?: Error;
  errorInfo?: React.ErrorInfo;
}

class ErrorBoundary extends React.Component<{ children: React.ReactNode }, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    this.setState({ errorInfo });
    console.error('[ErrorBoundary]', error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{ padding: 40 }}>
          <Result
            status="error"
            title="页面渲染异常"
            subTitle="请尝试刷新页面或联系管理员"
            extra={[
              <Button type="primary" key="reload" onClick={() => window.location.reload()}>
                刷新页面
              </Button>,
              <Button key="back" onClick={() => { this.setState({ hasError: false }); window.history.back(); }}>
                返回上一页
              </Button>,
            ]}
          >
            <div style={{ background: 'var(--lm-bg-elevated)', borderRadius: 8, padding: 16, marginTop: 16 }}>
              <Paragraph>
                <Text strong style={{ color: 'var(--lm-critical)' }}>
                  {this.state.error?.message}
                </Text>
              </Paragraph>
              <Paragraph style={{ fontSize: 12, color: 'var(--lm-text-tertiary)' }}>
                <pre style={{ maxHeight: 200, overflow: 'auto', fontSize: 11, whiteSpace: 'pre-wrap' }}>
                  {this.state.errorInfo?.componentStack}
                </pre>
              </Paragraph>
            </div>
          </Result>
        </div>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;
