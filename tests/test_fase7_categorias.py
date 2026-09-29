"""Suíte de testes automatizados para a Fase 7 — Módulo de Categorias e Orçamentos.

Verifica:
1. Listagem de categorias com consumo orçamentário (GET /api/categorias);
2. Criação de nova categoria (POST /api/categorias) e validação de nome único por tipo e usuário;
3. Edição do nome e teto da categoria (PUT /api/categorias/<id>);
4. Arquivamento e reativação lógica (PATCH);
5. Bloqueio de exclusão direta se houver movimentações ou fixos vinculados;
6. Exclusão direta quando vazia (sem lançamentos nem fixos);
7. Fluxo de reatribuição em lote atômica e exclusão (migrando lançamentos ativos,
   soft-deletados e fixos recorrentes para nova categoria do mesmo tipo);
8. Isolamento de dados entre usuários (anti-IDOR) retornando 404/403.
"""
from datetime import date
from decimal import Decimal
import unittest
from app import create_app
from app.models import db, Usuario, Conta, Categoria, Lancamento, LancamentoRecorrente
from config.config import Config


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False


class CategoriasTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        # Cria 2 usuários de teste
        self.user1 = Usuario(nome="Arthur Silva", email="arthur@teste.com", tema_preferido="dark")
        self.user1.definir_senha("SenhaForte123")
        self.user2 = Usuario(nome="Outro Usuário", email="outro@teste.com", tema_preferido="dark")
        self.user2.definir_senha("SenhaForte123")
        db.session.add_all([self.user1, self.user2])
        db.session.commit()

        # Cria conta padrão para o usuário 1
        self.conta1 = Conta(usuario_id=self.user1.id, nome="Carteira", saldo_inicial=Decimal("100.00"))
        db.session.add(self.conta1)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def autenticar(self, usuario_id):
        with self.client.session_transaction() as sess:
            sess["usuario_id"] = usuario_id
            sess["_csrf_token"] = "csrf-token-valido"

    def test_crud_categorias_e_regras_basicas(self):
        self.autenticar(self.user1.id)
        headers = {"X-CSRFToken": "csrf-token-valido"}

        # 1. Criação de categoria de despesa com teto de gastos
        res = self.client.post(
            "/api/categorias",
            json={"nome": "Alimentação", "tipo": "despesa", "teto_orcamento": 600.00},
            headers=headers,
        )
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertTrue(data["sucesso"])
        cat_id = data["dados"]["categoria"]["id"]
        self.assertEqual(data["dados"]["categoria"]["nome"], "Alimentação")
        self.assertEqual(data["dados"]["categoria"]["tipo"], "despesa")
        self.assertEqual(data["dados"]["categoria"]["teto_orcamento"], 600.00)

        # 2. Criação de categoria de receita
        res_rec = self.client.post(
            "/api/categorias",
            json={"nome": "Salário", "tipo": "receita"},
            headers=headers,
        )
        self.assertEqual(res_rec.status_code, 201)
        data_rec = res_rec.get_json()
        self.assertEqual(data_rec["dados"]["categoria"]["tipo"], "receita")
        self.assertIsNone(data_rec["dados"]["categoria"]["teto_orcamento"])

        # 3. Unicidade de nome para o mesmo tipo e usuário
        res_dup = self.client.post(
            "/api/categorias",
            json={"nome": "Alimentação", "tipo": "despesa"},
            headers=headers,
        )
        self.assertEqual(res_dup.status_code, 400)
        self.assertIn("já possui uma categoria de despesa com o nome", res_dup.get_json()["erro"])

        # Mas pode existir com o mesmo nome para tipos DIFERENTES (ex: "Outros" em receita e despesa)
        res_outro = self.client.post(
            "/api/categorias",
            json={"nome": "Alimentação", "tipo": "receita"},
            headers=headers,
        )
        self.assertEqual(res_outro.status_code, 201)

        # 4. Listagem de categorias
        res_list = self.client.get("/api/categorias")
        self.assertEqual(res_list.status_code, 200)
        lista = res_list.get_json()["dados"]
        self.assertEqual(len(lista["categorias"]), 3)
        self.assertEqual(len(lista["despesas"]), 1)
        self.assertEqual(len(lista["receitas"]), 2)

        # 5. Edição de categoria (nome e novo teto)
        res_edit = self.client.put(
            f"/api/categorias/{cat_id}",
            json={"nome": "Alimentação & Supermercado", "teto_orcamento": 750.00},
            headers=headers,
        )
        self.assertEqual(res_edit.status_code, 200)
        self.assertEqual(res_edit.get_json()["dados"]["categoria"]["nome"], "Alimentação & Supermercado")
        self.assertEqual(res_edit.get_json()["dados"]["categoria"]["teto_orcamento"], 750.00)

        # 6. Arquivamento e Reativação
        res_arq = self.client.patch(f"/api/categorias/{cat_id}/arquivar", headers=headers)
        self.assertEqual(res_arq.status_code, 200)
        self.assertEqual(res_arq.get_json()["dados"]["categoria"]["status"], "arquivado")

        res_reat = self.client.patch(f"/api/categorias/{cat_id}/reativar", headers=headers)
        self.assertEqual(res_reat.status_code, 200)
        self.assertEqual(res_reat.get_json()["dados"]["categoria"]["status"], "ativo")

        # 7. Exclusão física direta quando não há lançamentos vinculados
        res_del = self.client.delete(f"/api/categorias/{cat_id}", headers=headers)
        self.assertEqual(res_del.status_code, 200)
        self.assertTrue(res_del.get_json()["sucesso"])

        # Confirma que foi excluída do banco
        cat_consultada = db.session.get(Categoria, cat_id)
        self.assertIsNone(cat_consultada)

    def test_bloqueio_exclusao_com_historico_e_reatribuicao_em_lote(self):
        """Testa o bloqueio de exclusão quando há lançamentos e a reatribuição atômica."""
        self.autenticar(self.user1.id)
        headers = {"X-CSRFToken": "csrf-token-valido"}

        # Cria duas categorias de despesa
        cat_origem = Categoria(usuario_id=self.user1.id, nome="Mercado", tipo="despesa", teto_orcamento=Decimal("500.00"))
        cat_destino = Categoria(usuario_id=self.user1.id, nome="Supermercado Geral", tipo="despesa", teto_orcamento=Decimal("800.00"))
        db.session.add_all([cat_origem, cat_destino])
        db.session.commit()

        # Cria lançamentos vinculados: um ativo e um soft-deletado
        hoje = date.today()
        lanc_ativo = Lancamento(
            usuario_id=self.user1.id,
            tipo="despesa",
            valor=Decimal("120.00"),
            data_competencia=hoje,
            data_vencimento=hoje,
            descricao="Compras do mês",
            categoria_id=cat_origem.id,
            conta_id=self.conta1.id,
            forma_pagamento="PIX",
            status="pago",
        )
        lanc_deletado = Lancamento(
            usuario_id=self.user1.id,
            tipo="despesa",
            valor=Decimal("50.00"),
            data_competencia=hoje,
            data_vencimento=hoje,
            descricao="Lanche cancelado",
            categoria_id=cat_origem.id,
            conta_id=self.conta1.id,
            forma_pagamento="Dinheiro",
            status="pago",
        )
        lanc_deletado.soft_delete()

        # Cria modelo recorrente vinculado
        recorrente = LancamentoRecorrente(
            usuario_id=self.user1.id,
            tipo="despesa",
            valor=Decimal("80.00"),
            descricao="Feira semanal",
            categoria_id=cat_origem.id,
            conta_id=self.conta1.id,
            dia_vencimento=10,
            ativo=True,
        )

        db.session.add_all([lanc_ativo, lanc_deletado, recorrente])
        db.session.commit()

        # 1. Tentativa de exclusão direta: deve ser bloqueada e indicar requer_reatribuicao
        res_del_bloq = self.client.delete(f"/api/categorias/{cat_origem.id}", headers=headers)
        self.assertEqual(res_del_bloq.status_code, 400)
        data_bloq = res_del_bloq.get_json()
        self.assertFalse(data_bloq["sucesso"])
        self.assertTrue(data_bloq["requer_reatribuicao"])
        self.assertEqual(data_bloq["total_lancamentos"], 2)
        self.assertEqual(data_bloq["total_recorrentes"], 1)

        # 2. Reatribuição em lote com erro proposital (mesma categoria)
        res_mesma = self.client.post(
            f"/api/categorias/{cat_origem.id}/reatribuir-excluir",
            json={"nova_categoria_id": cat_origem.id},
            headers=headers,
        )
        self.assertEqual(res_mesma.status_code, 400)

        # 3. Reatribuição em lote com categoria de outro tipo (deve falhar)
        cat_receita = Categoria(usuario_id=self.user1.id, nome="Salário", tipo="receita")
        db.session.add(cat_receita)
        db.session.commit()

        res_tipo_dif = self.client.post(
            f"/api/categorias/{cat_origem.id}/reatribuir-excluir",
            json={"nova_categoria_id": cat_receita.id},
            headers=headers,
        )
        self.assertEqual(res_tipo_dif.status_code, 400)
        self.assertIn("mesmo tipo", res_tipo_dif.get_json()["erro"])

        # 4. Reatribuição em lote bem-sucedida para cat_destino
        res_reatr = self.client.post(
            f"/api/categorias/{cat_origem.id}/reatribuir-excluir",
            json={"nova_categoria_id": cat_destino.id},
            headers=headers,
        )
        self.assertEqual(res_reatr.status_code, 200)
        data_reatr = res_reatr.get_json()
        self.assertTrue(data_reatr["sucesso"])
        self.assertEqual(data_reatr["dados"]["total_lancamentos_transferidos"], 2)
        self.assertEqual(data_reatr["dados"]["total_recorrentes_transferidos"], 1)

        # Validação no banco:
        # A categoria de origem não existe mais
        self.assertIsNone(db.session.get(Categoria, cat_origem.id))

        # Os lançamentos agora pertencem a cat_destino
        db.session.refresh(lanc_ativo)
        db.session.refresh(lanc_deletado)
        db.session.refresh(recorrente)
        self.assertEqual(lanc_ativo.categoria_id, cat_destino.id)
        self.assertEqual(lanc_deletado.categoria_id, cat_destino.id)
        self.assertEqual(recorrente.categoria_id, cat_destino.id)

    def test_anti_idor_e_seguranca(self):
        """Garante que um usuário não pode acessar nem manipular categorias de outro usuário."""
        # Cria categoria para Usuário 1
        cat_user1 = Categoria(usuario_id=self.user1.id, nome="Farmácia", tipo="despesa")
        db.session.add(cat_user1)
        db.session.commit()

        # Autentica como Usuário 2
        self.autenticar(self.user2.id)
        headers = {"X-CSRFToken": "csrf-token-valido"}

        # Usuário 2 tenta editar categoria do Usuário 1 (deve retornar 404/403)
        res_edit = self.client.put(
            f"/api/categorias/{cat_user1.id}",
            json={"nome": "Farmácia Hackeada"},
            headers=headers,
        )
        self.assertEqual(res_edit.status_code, 404)

        # Usuário 2 tenta arquivar
        res_arq = self.client.patch(f"/api/categorias/{cat_user1.id}/arquivar", headers=headers)
        self.assertEqual(res_arq.status_code, 404)

        # Usuário 2 tenta excluir
        res_del = self.client.delete(f"/api/categorias/{cat_user1.id}", headers=headers)
        self.assertEqual(res_del.status_code, 404)

        # Categoria deve permanecer intocada
        cat_check = db.session.get(Categoria, cat_user1.id)
        self.assertEqual(cat_check.nome, "Farmácia")
        self.assertEqual(cat_check.status, "ativo")


if __name__ == "__main__":
    unittest.main()
