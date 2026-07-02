import React, { useEffect, useState } from 'react';
import { Card, Table, Button, Space, Typography, Modal, Form, Input, message, Tag, Descriptions, Upload } from 'antd';
import { PlusOutlined, DeleteOutlined, EyeOutlined, UploadOutlined, ReloadOutlined, BookOutlined } from '@ant-design/icons';
import { ragApi } from '@/api/services';

const { Title } = Typography;

const KnowledgeBase: React.FC = () => {
  const [kbs, setKbs] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [detailKB, setDetailKB] = useState<any>(null);
  const [docs, setDocs] = useState<any[]>([]);
  const [uploading, setUploading] = useState(false);

  const fetchKBs = async () => {
    setLoading(true);
    try {
      const { data } = await ragApi.listKBs();
      setKbs(Array.isArray(data) ? data : (data?.items || []));
    } catch (err) { console.error(err); }
    finally { setLoading(false); }
  };

  useEffect(() => { fetchKBs(); }, []);

  const handleCreate = async (values: any) => {
    try {
      await ragApi.createKB(values);
      message.success('知识库已创建');
      setCreateOpen(false);
      fetchKBs();
    } catch (err: any) { message.error(err.response?.data?.detail || '创建失败'); }
  };

  const handleDelete = async (id: string) => {
    try {
      await ragApi.deleteKB(id);
      message.success('已删除');
      fetchKBs();
    } catch { message.error('删除失败'); }
  };

  const openDetail = async (kb: any) => {
    setDetailKB(kb);
    try {
      const { data } = await ragApi.listDocs(kb.id);
      setDocs(data || []);
    } catch { setDocs([]); }
  };

  const handleUpload = async (file: File) => {
    if (!detailKB?.id) return false;
    setUploading(true);
    try {
      const content = await file.text();
      await ragApi.uploadDoc(detailKB.id, {
        filename: file.name,
        content,
        metadata: { source: 'ui_upload' },
      });
      message.success('文档已上传，正在索引');
      await openDetail(detailKB);
    } catch (err: any) {
      message.error(err.response?.data?.detail || '上传失败');
    } finally {
      setUploading(false);
    }
    return false;
  };

  const columns = [
    { title: '名称', dataIndex: 'name', width: 200 },
    { title: '描述', dataIndex: 'description', ellipsis: true },
    { title: '嵌入模型', dataIndex: 'embedding_model', width: 150 },
    {
      title: '操作', width: 150,
      render: (_: any, r: any) => (
        <Space>
          <Button size="small" icon={<EyeOutlined />} onClick={() => openDetail(r)}>详情</Button>
          <Button size="small" danger icon={<DeleteOutlined />} onClick={() => Modal.confirm({ title: '确认删除?', onOk: () => handleDelete(r.id) })} />
        </Space>
      ),
    },
  ];

  const docColumns = [
    { title: '文件名', dataIndex: 'filename', ellipsis: true },
    { title: '大小', dataIndex: 'file_size', width: 100, render: (v: number) => v ? `${(v / 1024).toFixed(1)}KB` : '-' },
    { title: '状态', dataIndex: 'status', width: 80, render: (v: string) => <Tag color={v === 'indexed' ? '#52c41a' : '#fa8c16'}>{v}</Tag> },
  ];

  return (
    <div className="lm-animate-in">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <Title level={4} style={{ margin: 0, color: 'var(--lm-text)' }}>
          <BookOutlined style={{ marginRight: 8 }} />知识库
        </Title>
        <Space>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建知识库</Button>
          <Button icon={<ReloadOutlined />} onClick={fetchKBs} />
        </Space>
      </div>

      <Card style={{ background: 'var(--lm-bg-card)', border: '1px solid var(--lm-border-light)', borderRadius: 12 }} styles={{ body: { padding: 0 } }}>
        <Table dataSource={kbs} columns={columns} rowKey="id" size="small" loading={loading} pagination={false} />
      </Card>

      {/* Create Modal */}
      <Modal title="新建知识库" open={createOpen} onCancel={() => setCreateOpen(false)} footer={null} destroyOnClose>
        <Form layout="vertical" onFinish={handleCreate}>
          <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="description" label="描述"><Input.TextArea rows={2} /></Form.Item>
          <Form.Item name="embedding_model" label="嵌入模型" initialValue="text-embedding-3-small"><Input /></Form.Item>
          <Form.Item><Button type="primary" htmlType="submit" block>创建</Button></Form.Item>
        </Form>
      </Modal>

      {/* Detail Drawer as Modal */}
      <Modal title={detailKB?.name || '知识库详情'} open={!!detailKB} onCancel={() => setDetailKB(null)} footer={null} width={700}>
        {detailKB && (
          <>
            <Descriptions size="small" column={2} style={{ marginBottom: 16 }}>
              <Descriptions.Item label="ID">{detailKB.id}</Descriptions.Item>
              <Descriptions.Item label="嵌入模型">{detailKB.embedding_model}</Descriptions.Item>
              <Descriptions.Item label="描述" span={2}>{detailKB.description || '-'}</Descriptions.Item>
            </Descriptions>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <Title level={5} style={{ margin: 0 }}>文档列表</Title>
              <Upload
                showUploadList={false}
                beforeUpload={(file) => handleUpload(file)}
                accept=".txt,.md,.log,.json,.yaml,.yml"
              >
                <Button size="small" icon={<UploadOutlined />} loading={uploading}>上传文档</Button>
              </Upload>
            </div>
            <Table dataSource={docs} columns={docColumns} rowKey="id" size="small" pagination={false} />
          </>
        )}
      </Modal>
    </div>
  );
};

export default KnowledgeBase;
