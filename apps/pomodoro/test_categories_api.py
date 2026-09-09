from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_api_key.models import APIKey

from .models import Category, Group


class CategoryViewSetTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        _, cls.api_key = APIKey.objects.create_key(name='categories-key')
        cls.group = Group.objects.create(name='Estudos')
        cls.category = Category.objects.create(name='Leitura', group=cls.group)

    def setUp(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Api-Key {self.api_key}')

    def test_list_returns_category_name_and_group(self):
        response = self.client.get('/api/categories/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        category = next(
            item for item in response.data if item['id'] == self.category.id
        )
        self.assertEqual(
            category,
            {
                'id': self.category.id,
                'name': 'Leitura',
                'color': self.category.color,
                'group': self.group.id,
                'group_name': 'Estudos',
            },
        )

    def test_list_requires_api_key(self):
        self.client.credentials()

        response = self.client.get('/api/categories/')

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
